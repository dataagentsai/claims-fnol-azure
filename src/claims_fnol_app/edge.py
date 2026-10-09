"""The app a browser talks to: the harness's edge, with this insurer's pages.

`agent_harness.serve` is `/chat`, `/feedback`, `/healthz` and the desk's API
under `/ops`; the chat page and the handler page are `claims_fnol_app.pages`,
handed to it the way the reference agent's `serve`/`reviewer` hand theirs.
Around it, three routes of this app's own:

    /signin       local test sign-in (local and test only)
    /opening      what a signed-in policyholder is shown first: their open claims
                  and policies, with no model call (P-OPEN)
    /dev/usage    model calls and tokens so far (local and test only)
"""

from __future__ import annotations

from agent_harness import identity as ident
from agent_harness import serve
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import BaseRoute, Mount, Route

from claims_fnol import entrypoint as ep
from claims_fnol_app import signin
from claims_fnol_app.pages import CHAT_PAGE, desk_router
from claims_fnol_app.usage import Counted
from claims_fnol_app.waits import Waits


def _policyholder(request: Request, issuer: ident.Issuer) -> ident.Principal | Response:
    header = request.headers.get("authorization", "")
    token = header[7:].strip() if header[:7].lower() == "bearer " else ""
    try:
        return ident.verify(token, issuer=issuer)
    except ident.InvalidSession:
        return JSONResponse({"error": "the session token is not valid"}, status_code=401)


def opening(agent: ep.Agent, issuer: ident.Issuer) -> Route:
    async def show(request: Request) -> Response:
        who = _policyholder(request, issuer)
        if isinstance(who, Response):
            return who
        if not who.customer_id:
            return JSONResponse({"error": "this session is not a policyholder's"}, 403)
        return JSONResponse({"reply": await agent.opening(who.as_customer())})

    return Route("/opening", show)


def usage(counted: Counted) -> Route:
    async def totals(_: Request) -> Response:
        return JSONResponse(counted.totals())

    return Route("/dev/usage", totals)


def build(
    agent: ep.Agent, held: Waits, issuer: signin.LocalIssuer, *, counted: Counted, env: str
) -> Starlette:
    """The whole app. One agent, one set of waits, one issuer."""
    verifier = issuer.issuer()
    served = serve.build(
        agent,
        issuer=verifier,
        chat_page=CHAT_PAGE,
        # The DBOS desks have the Temporal desks' shape; serve types the latter (F-20).
        desk=held.desk,  # type: ignore[arg-type]
        approvals=held.approvals,
        approver=held.approver,  # type: ignore[arg-type]
        desk_pages=(desk_router,),
    )
    routes: list[BaseRoute] = [opening(agent, verifier)]
    if env in ("local", "test"):
        routes += [
            Route("/signin", signin.signin_page, methods=["GET"]),
            Route("/signin", signin.signin, methods=["POST"]),
            usage(counted),
        ]
    app = Starlette(routes=[*routes, Mount("/", app=served)])
    app.state.local_issuer = issuer
    return app


__all__ = ["build", "opening", "usage"]
