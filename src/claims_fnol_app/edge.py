"""The app a browser talks to: the harness's edge, with this insurer's pages.

`agent_harness.serve` is `/chat`, `/feedback`, `/healthz` and the desk's API
under `/ops`; the chat page and the handler page are `claims_fnol_app.pages`,
handed to it the way the reference agent's `serve`/`reviewer` hand theirs.
Around it, three routes of this app's own:

    /signin       local test sign-in, where the identity adapter can sign
                  (`local-dev`); no other adapter can, so it exists nowhere else
    /.well-known/jwks.json
                  that signer's public keys, so the claims system can verify
                  the tokens it mints for it (A1); only where there is a signer
    /opening      what a signed-in policyholder is shown first: their open claims
                  and policies, with no model call (P-OPEN)
    /dev/usage    model calls and tokens so far, where the overlay asks for it
                  (`app.usage_route`)

No route asks which environment this is: each is mounted for what the composed
adapters can do or what the overlay says. Every route checks a session with the
verifier of the identity adapter the overlay chose (`Sessions.verify`), never
with one issuer's shape (F-30).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from agent_harness import identity as ident
from agent_harness import serve
from agent_harness.adapters.identity import Sessions
from agent_harness.adapters.waits import Waits
from agent_harness.entrypoint import TurnAgent
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import BaseRoute, Mount, Route

from claims_fnol_app import signin
from claims_fnol_app.pages import CHAT_PAGE, desk_router
from claims_fnol_app.usage import Counted, Logged


def _policyholder(request: Request, sessions: Sessions) -> ident.Principal | Response:
    header = request.headers.get("authorization", "")
    token = header[7:].strip() if header[:7].lower() == "bearer " else ""
    try:
        return sessions.verify(token)
    except ident.InvalidSession:
        return JSONResponse({"error": "the session token is not valid"}, status_code=401)


def opening(agent: TurnAgent, sessions: Sessions) -> Route:
    async def show(request: Request) -> Response:
        who = _policyholder(request, sessions)
        if isinstance(who, Response):
            return who
        if not who.customer_id:
            return JSONResponse({"error": "this session is not a policyholder's"}, 403)
        return JSONResponse({"reply": await agent.opening(who.as_customer())})

    return Route("/opening", show)


def published(jwks: dict[str, object]) -> Callable[[Request], Awaitable[Response]]:
    """The local issuer's public keys: what a far end verifies its tokens with."""

    async def keys(_: Request) -> Response:
        return JSONResponse(jwks)

    return keys


def usage(counted: Counted) -> Route:
    async def totals(_: Request) -> Response:
        return JSONResponse(counted.totals())

    return Route("/dev/usage", totals)


def build(
    agent: TurnAgent, held: Waits, sessions: Sessions, *, counted: Counted, usage_route: bool
) -> Starlette:
    """The whole app. One agent, one set of waits, one identity."""
    served = serve.build(
        Logged(agent),
        # The identity adapter's own verifier, at /chat, /feedback and the desk
        # alike: an Entra handler's `roles` and `scp` read as Entra writes them
        # (FINDINGS F-30), the local and Keycloak issuers' as theirs.
        verify=sessions.verify,
        chat_page=CHAT_PAGE,
        # The DBOS desks have the Temporal desks' shape; serve types the latter (F-20).
        desk=held.desk,
        approvals=held.approvals,
        approver=held.approver,
        desk_pages=(desk_router,),
    )
    routes: list[BaseRoute] = [opening(agent, sessions)]
    if sessions.signer is not None:
        routes += [
            Route("/signin", signin.signin_page, methods=["GET"]),
            Route("/signin", signin.signin, methods=["POST"]),
            Route("/.well-known/jwks.json", published(sessions.signer.jwks), methods=["GET"]),
        ]
    if usage_route:
        routes.append(usage(counted))
    app = Starlette(routes=[*routes, Mount("/", app=served)])
    app.state.local_issuer = sessions.signer
    return app


__all__ = ["build", "opening", "usage"]
