"""Local sign-in: three test users, signed by the `local-dev` identity adapter.

The issuer is the library's (`agent_harness.identity.local`, bound by the
overlay's `identity: local-dev`): an RS256 key made once per process, sessions
signed with the private half, the agent verifying with the public half exactly
as it would with a real realm. What is this agent's is who the test users are
and where each lands. The page is mounted only where the identity adapter can
sign (`edge.build`); an overlay binding Entra ID has the code flow at `/signin`
instead (`signin_flow.py`, A2). Sessions die with the process: restart, sign in again.
"""

from __future__ import annotations

import html
import urllib.parse
from dataclasses import dataclass

from agent_harness import identity as ident
from agent_harness.identity.local import LocalIssuer
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response

from claims_fnol.binding import POLICYHOLDER_SCOPES

URL = "http://local-issuer.test/realms/claims"
AUDIENCE = "claims-fnol"
HANDLER_SCOPES = ident.REVIEWER_SCOPES | ident.APPROVER_SCOPES


@dataclass(frozen=True)
class TestUser:
    login: str
    name: str
    role: str
    policyholder_id: str | None = None


USERS = (
    TestUser("rohan", "Rohan Iyer", "policyholder", "PH-1001"),
    TestUser("meera", "Meera Khanna", "policyholder", "PH-1002"),
    TestUser("asha", "Asha Rao", "claims handler"),
)
"""The FNOL world's two policyholders, and one claims handler who decides
payouts and works the escalation queue."""


def mint(signer: LocalIssuer, user: TestUser) -> str:
    """A session for a test user, signed by the `local-dev` identity adapter."""
    return signer.mint(
        subject=f"login-{user.login}",
        scopes=POLICYHOLDER_SCOPES if user.policyholder_id else HANDLER_SCOPES,
        customer_id=user.policyholder_id,
        name=user.name,
    )


def landing(signer: LocalIssuer, user: TestUser) -> str:
    """Where a signed-in user goes: the chat, or the handler page."""
    where = "/" if user.policyholder_id else "/ops/desk"
    return f"{where}?token={mint(signer, user)}"


def page() -> str:
    rows = "".join(
        f'<form method="post" action="/signin"><input type="hidden" name="login" '
        f'value="{html.escape(u.login)}"><button>{html.escape(u.name)}</button>'
        f"<span>{html.escape(u.role)}"
        f"{' · ' + html.escape(u.policyholder_id) if u.policyholder_id else ''}</span></form>"
        for u in USERS
    )
    return SIGNIN_PAGE.replace("__USERS__", rows)


SIGNIN_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Sign in</title>
<style>
:root{--bg:#F4F6F8;--card:#fff;--ink:#141A21;--dim:#5C6670;--line:#DCE1E7;--accent:#2A5A8C}
@media (prefers-color-scheme:dark){:root{--bg:#0F1418;--card:#161C22;--ink:#E4E9EE;
  --dim:#9AA6B1;--line:#26303A;--accent:#7FB0DC}}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,sans-serif}
main{max-width:30rem;margin:12vh auto;padding:0 16px}
h1{font-size:20px;margin:0 0 4px} p{color:var(--dim);margin:0 0 20px;font-size:14px}
form{display:flex;align-items:center;gap:12px;background:var(--card);
  border:1px solid var(--line);border-radius:10px;padding:12px 14px;margin-bottom:10px}
button{font:600 15px system-ui,sans-serif;padding:8px 16px;border-radius:8px;border:0;
  background:var(--accent);color:#fff;cursor:pointer;min-width:9rem}
span{color:var(--dim);font-size:13px}
</style></head><body><main>
<h1>Motor claims · local sign-in</h1>
<p>Test users on this Mac only. Pick one; the link you land on carries the session.</p>
__USERS__
</main></body></html>
"""


async def signin_page(_: Request) -> Response:
    return HTMLResponse(page())


async def signin(request: Request) -> Response:
    """Mint the chosen test user's session and send them on, with it in the link."""
    form = urllib.parse.parse_qs((await request.body()).decode())
    chosen = {u.login: u for u in USERS}.get((form.get("login") or [""])[0])
    if chosen is None:
        return HTMLResponse("no such test user", status_code=400)
    signer: LocalIssuer = request.app.state.local_issuer
    return RedirectResponse(landing(signer, chosen), status_code=303)


async def signout(_: Request) -> Response:
    """Back to the test page. A local session lives only in the page that holds
    it; nothing is stored, so there is nothing to end here."""
    return RedirectResponse("/signin", status_code=303)


__all__ = [
    "AUDIENCE",
    "HANDLER_SCOPES",
    "URL",
    "USERS",
    "TestUser",
    "landing",
    "mint",
    "signin",
    "signout",
]
