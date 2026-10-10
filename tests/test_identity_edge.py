"""F-30 — this app's edge checks sessions with the identity adapter its overlay chose.

The library's contract table (reference-agent `tests/test_adapter_contracts.py`,
`test_the_edges_verify_through_the_identity_port`) holds every identity adapter
to the same rows at `serve` and the desk. This table holds *this* app to them,
with each overlay's own identity settings: the local test sign-in
(`config/local.yaml`, `local-dev`) and Entra (`config/azure.yaml`, `entra-id`,
its app role `desk.handler` mapped to the desk's scopes by `role_scopes`).

Each session is written in its issuer's own shape — Keycloak-style `scp` lists
and `jti` locally, Entra's `scp` string, `roles` and `uti` on Azure — and signed
in this process. No network: the Entra key set is the `keys` hook.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx2
import jwt
import pytest
from agent_harness import adapters
from agent_harness import identity as ident
from agent_harness.adapters import Wiring
from agent_harness.adapters.identity import Sessions
from agent_harness.adapters.waits import Waits
from agent_harness.identity.local import KID, LocalIssuer
from agent_harness.state import InMemoryCheckpointStore

from claims_fnol.binding import POLICYHOLDER_SCOPES
from claims_fnol_app import edge, signin
from claims_fnol_app.compose import overlay
from claims_fnol_app.usage import Counted

TENANT = "00000000-0000-0000-0000-0000000000fe"
ENTRA_KEY = LocalIssuer()
"""Signs the Entra-shaped sessions; its public half is the `keys` hook."""
REFERENCES = {
    "AZURE_TENANT_ID": TENANT,
    "ENTRA_APP_ID": "11111111-0000-0000-0000-00000000a9e7",
    "agent-obo-client-secret": "test-secret",
}


def _resolved(value: Any) -> Any:
    """An overlay's `{env: NAME}` or `{key_vault: name}` replaced by this test's
    value (no secrets port)."""
    if adapters.is_reference(value):
        return REFERENCES[value.get("env") or value["key_vault"]]
    return value


@asynccontextmanager
async def sessions_of(environment: str) -> AsyncIterator[Sessions]:
    """The identity port exactly as `config/<environment>.yaml` binds it."""
    bound = adapters.plan(overlay(environment)).bound["identity"]
    settings = {k: _resolved(v) for k, v in bound.settings.items()}
    hooks = {"keys": ident.JWKS(ENTRA_KEY.jwks)}
    async with bound.adapter.build(Wiring(settings, {}, hooks, environment)) as made:
        yield made


def mint(environment: str, sessions: Sessions, *, handler: bool) -> str:
    """A handler's or a policyholder's session, in the shape that issuer writes."""
    if sessions.signer is not None:  # local-dev: the app's own sign-in mints it
        user = next(u for u in signin.USERS if (u.policyholder_id is None) == handler)
        return signin.mint(sessions.signer, user)
    now = int(time.time())
    claims: dict[str, Any] = {
        "iss": f"https://login.microsoftonline.com/{TENANT}/v2.0",
        "aud": sessions.issuer.audience,
        "sub": "login-asha" if handler else "login-rohan",
        "uti": uuid.uuid4().hex,
        "iat": now,
        "exp": now + 600,
    }
    if handler:
        claims["roles"] = ["desk.handler"]
    else:
        claims["scp"] = " ".join(sorted(POLICYHOLDER_SCOPES))
        claims[ident.CLAIM_CUSTOMER] = "PH-1001"
    return jwt.encode(claims, ENTRA_KEY._key, algorithm="RS256", headers={"kid": KID})


class _EmptyQueue:
    async def pending(self) -> tuple[()]:
        return ()

    async def get(self, _: str) -> None:
        return None

    async def open_for(self, _: str) -> None:
        return None


class _Agent:
    """Stands in for `claims_fnol.entrypoint.Agent`: only `/opening` is asked of it."""

    def __init__(self) -> None:
        self.store = InMemoryCheckpointStore()
        self.escalations = _EmptyQueue()

    async def opening(self, identity: Any) -> str:
        return f"hello {identity.customer_id}"

    async def handle(self, *_: Any, **__: Any) -> Any:
        raise AssertionError("no turn is run here")


OVERLAYS = ["local", "azure"]
# (row, a handler's session or a policyholder's, method, path, the status)
ROWS: list[tuple[str, bool, str, str, int]] = [
    (
        "a handler with the desk role reads what waits for approval",
        True,
        "GET",
        "/ops/approvals",
        200,
    ),
    (
        "a handler with the desk role reads the escalation queue",
        True,
        "GET",
        "/ops/escalations",
        200,
    ),
    (
        "a policyholder without the desk role is refused at the desk",
        False,
        "GET",
        "/ops/approvals",
        403,
    ),
    ("a policyholder is shown their opening", False, "GET", "/opening", 200),
    ("a handler is not a policyholder at the opening", True, "GET", "/opening", 403),
    ("a policyholder's session is accepted at /feedback", False, "POST", "/feedback", 404),
]


@pytest.mark.parametrize("environment", OVERLAYS)
@pytest.mark.parametrize(
    ("row", "handler", "method", "path", "status"), ROWS, ids=[r[0] for r in ROWS]
)
async def test_the_edge_verifies_with_the_overlays_identity_adapter(
    environment: str, row: str, handler: bool, method: str, path: str, status: int
) -> None:
    async with sessions_of(environment) as sessions:
        queue = _EmptyQueue()
        waits = Waits(approvals=queue, escalations=queue, approver=None, desk=None)  # type: ignore[arg-type]
        app = edge.build(
            _Agent(),  # type: ignore[arg-type]
            waits,
            sessions,
            counted=Counted(inner=None),  # type: ignore[arg-type]
            usage_route=False,
        )
        token = mint(environment, sessions, handler=handler)
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url="http://fnol.test"
        ) as http:
            answer = await http.request(
                method,
                path,
                headers={"authorization": f"Bearer {token}"},
                json={"conversation_id": "cnv_none", "value": "up"} if method == "POST" else None,
            )
    assert answer.status_code == status, (row, answer.text)


@pytest.mark.parametrize("environment", OVERLAYS)
async def test_the_desk_role_grants_what_the_local_handler_holds(environment: str) -> None:
    """Entra's `desk.handler` role is worth exactly the local handler's scopes,
    so the desk behaves the same on the Mac and on Azure."""
    async with sessions_of(environment) as sessions:
        held = sessions.verify(mint(environment, sessions, handler=True)).scopes
    assert held >= signin.HANDLER_SCOPES
    assert not held & POLICYHOLDER_SCOPES
