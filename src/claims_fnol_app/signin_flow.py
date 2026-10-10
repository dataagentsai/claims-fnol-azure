"""Sign-in through the identity provider (A2): the code flow, where the overlay gives one.

The other half of the sign-in port. `signin.py` is the local test page, mounted
where the identity adapter can sign (`local-dev`); this is mounted where it has
a browser sign-in instead (`entra-id` with `login_redirect_uri`, the Azure
overlay). Neither asks which environment it is (`edge.build`).

    GET  /signin            to Entra's /authorize: state, PKCE (S256) and a nonce,
                            sealed into a 10-minute cookie only /signin/* is sent
    GET  /signin/callback   the state checked, the code redeemed with the verifier,
                            the tokens checked (`EntraLogin.signed_in`), the
                            refresh token stored encrypted, and the browser sent on
    POST /signin/refresh    a fresh access token from the stored refresh token
    POST /signin/logout     the stored login deleted, the cookie cleared, and on
                            to Entra's logout

The session is kept as the app keeps it today: the access token in the page's
memory, sent as `Authorization: Bearer` and verified at every route by the
identity adapter's verifier (F-30). It travels in the link's fragment, which a
browser never sends to a server, so it is in no access log. The cookie names the
login and nothing else; the refresh token never reaches the browser.

Where a signed-in user lands is this agent's: a policyholder (the holder claim)
at the chat, a claims handler (`desk.handler`, mapped to the desk's scopes) at
the desk, and anyone else is told why there is nothing for them. Every refusal
is a fixed message; why is for the operator's span, not for whoever is probing.
"""

from __future__ import annotations

import hmac
import time
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass, field

from agent_harness import identity as ident
from agent_harness import telemetry as tel
from agent_harness.contracts import SessionStore, StoredSession
from agent_harness.contracts.failures import AgentFailure
from agent_harness.identity.entra_login import EntraLogin, Flow, Sealer
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, RedirectResponse, Response
from starlette.routing import Route

FLOW_COOKIE = "signin_flow"
SESSION_COOKIE = "signin_session"
FLOW_TTL_S = 600
SESSION_TTL_S = 12 * 3600
"""How long a sign-in is kept for refreshing. After it, or after a logout, the
next refresh is refused and the page sends the browser to /signin again."""

AGAIN = "This sign-in could not be completed. Please start again at /signin."
UNAVAILABLE = "Signing in is unavailable just now. Please try again in a minute."
NOT_LINKED = (
    "You are signed in, but your account is not linked to a policy yet, so there is "
    "nothing to show you here. Please contact us and we will link it."
)
DESK_SCOPES = (ident.SCOPE_APPROVALS_DECIDE, ident.SCOPE_ESCALATIONS_REVIEW)


@dataclass(frozen=True)
class CodeFlow:
    """What the routes read, held on the app's state."""

    login: EntraLogin
    sealer: Sealer
    verify: ident.Verifier
    stored: SessionStore | None = None
    """Where refresh tokens are kept; without one, none is kept (no refresh)."""
    clock: Callable[[], float] = field(default=time.time)

    @property
    def secure(self) -> bool:
        return self.login.redirect_uri.startswith("https://")

    @property
    def home(self) -> str:
        """The app's own origin, where Entra's logout sends the browser back."""
        parts = urllib.parse.urlsplit(self.login.redirect_uri)
        return f"{parts.scheme}://{parts.netloc}/"


def landing(who: ident.Principal) -> str | None:
    """The chat for a policyholder, the desk for a claims handler, else nowhere."""
    if who.customer_id:
        return "/"
    if any(who.may(scope) for scope in DESK_SCOPES):
        return "/ops/desk"
    return None


def _flow(request: Request) -> CodeFlow:
    flow: CodeFlow = request.app.state.code_flow
    return flow


def _cookie(flow: CodeFlow, response: Response, name: str, value: str, ttl_s: int) -> None:
    response.set_cookie(
        name,
        value,
        max_age=ttl_s,
        path="/signin",
        httponly=True,
        secure=flow.secure,
        samesite="lax",
    )


def _refused(status: int, message: str, detail: str) -> Response:
    with tel.span("agent.signin.refused", **{"http.refusal_detail": detail}):
        pass
    return PlainTextResponse(message, status_code=status)


async def start(request: Request) -> Response:
    flow, sign_in = _flow(request), Flow()
    response = RedirectResponse(flow.login.authorize_url(sign_in), status_code=303)
    _cookie(flow, response, FLOW_COOKIE, flow.sealer.seal(sign_in.sealed()), FLOW_TTL_S)
    return response


async def callback(request: Request) -> Response:
    flow = _flow(request)
    sign_in = Flow.opened(flow.sealer.open(request.cookies.get(FLOW_COOKIE), FLOW_TTL_S))
    state, code = request.query_params.get("state"), request.query_params.get("code")
    if sign_in is None or not state or not code or not hmac.compare_digest(state, sign_in.state):
        return _refused(400, AGAIN, "state")
    try:
        tokens = await flow.login.redeem(code, sign_in)
        who = flow.login.signed_in(tokens, sign_in, flow.verify)
    except ident.InvalidSession as exc:
        return _refused(400, AGAIN, str(exc))
    except AgentFailure as exc:  # Entra unreachable, misconfigured or malformed
        return _refused(503, UNAVAILABLE, type(exc).__name__)
    where = landing(who)
    if where is None:
        return _refused(403, NOT_LINKED, "no holder claim and no desk role")
    now = int(flow.clock())
    if flow.stored is not None and tokens.refresh:
        await flow.stored.expire(now - SESSION_TTL_S)  # logins nobody refreshed: gone
        await flow.stored.put(
            StoredSession(subject=who.subject, refresh_token=tokens.refresh, updated_at=now)
        )
    response = RedirectResponse(f"{where}#token={tokens.access}", status_code=303)
    sealed = flow.sealer.seal({"sub": who.subject})
    _cookie(flow, response, SESSION_COOKIE, sealed, SESSION_TTL_S)
    response.delete_cookie(FLOW_COOKIE, path="/signin")
    return response


async def refresh(request: Request) -> Response:
    """A fresh access token for the page, or 401: sign in again."""
    flow = _flow(request)
    session = flow.sealer.open(request.cookies.get(SESSION_COOKIE), SESSION_TTL_S)
    stored = await flow.stored.get(str(session["sub"])) if session and flow.stored else None
    if session is None or stored is None or flow.stored is None:
        return JSONResponse({"error": "sign in again", "signin": "/signin"}, status_code=401)
    subject = str(session["sub"])
    access, keep, who = "", "", None
    try:
        access, keep = await flow.login.refresh(stored.refresh_token)
        who = flow.verify(access)
    except ident.InvalidSession:
        pass
    except AgentFailure:
        return JSONResponse({"error": UNAVAILABLE}, status_code=503)
    if who is None or who.subject != subject:
        await flow.stored.delete(subject)
        return JSONResponse({"error": "sign in again", "signin": "/signin"}, status_code=401)
    now = int(flow.clock())
    await flow.stored.put(StoredSession(subject=subject, refresh_token=keep, updated_at=now))
    return JSONResponse({"token": access})


async def logout(request: Request) -> Response:
    flow = _flow(request)
    session = flow.sealer.open(request.cookies.get(SESSION_COOKIE), SESSION_TTL_S)
    if session is not None and flow.stored is not None:
        await flow.stored.delete(str(session["sub"]))
    response = RedirectResponse(flow.login.logout_url(flow.home), status_code=303)
    response.delete_cookie(SESSION_COOKIE, path="/signin")
    return response


def routes() -> list[Route]:
    return [
        Route("/signin", start, methods=["GET"]),
        Route("/signin/callback", callback, methods=["GET"]),
        Route("/signin/refresh", refresh, methods=["POST"]),
        Route("/signin/logout", logout, methods=["POST"]),
    ]


__all__ = ["NOT_LINKED", "CodeFlow", "landing", "routes"]
