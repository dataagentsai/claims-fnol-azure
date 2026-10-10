"""A1 — the claims system verifies the caller's token, and never the agent's word.

At the edge: the claims system's own MCP server over streamable HTTP, on a
throwaway database seeded with the FNOL world, its `authorise` port built from
its own overlay (`config/claims-system/local.yaml`, `local-dev`; and
`azure.yaml`, `entra-id`) through the registry. Every token is signed in this
process, in the shape its issuer writes, and sent as the request's
`Authorization` header — what the agent's MCP client does (reference-agent
`tools.mcp.BearerFromSession`). The `_meta` session always asserts Rohan, so a
row served on someone else's word would show.

The same rows on both adapters. And one row that matters more than the rest:
the Azure overlay cannot bind `asserted` at all.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx2
import jwt
import pytest
import uvicorn
from agent_harness import adapters
from agent_harness import identity as ident
from agent_harness.adapters import OverlayRefused
from agent_harness.contracts import Approval, ApprovalState
from agent_harness.contracts.records import approval_record
from agent_harness.identity.local import LocalIssuer
from agent_harness.state.records import InMemoryRecords
from kit import MEERA, ROHAN
from mcp.client import Client
from mcp.client.streamable_http import streamable_http_client
from pg import throwaway

from claims_system import server as srv
from claims_system import store as st
from claims_system.__main__ import authorisation, overlay, world_records

TENANT = "00000000-0000-0000-0000-0000000000fe"
CLAIMS_APP = "22222222-0000-0000-0000-0000000c1a15"
"""The claims system's app id: a v2 token's `aud` (F-55)."""
LOCAL_ISSUER = "http://local-issuer.test/realms/claims"
ENTRA_ISSUER = f"https://login.microsoftonline.com/{TENANT}/v2.0"
SIGNER = LocalIssuer()
STRANGER = LocalIssuer()
"""Another key under the same id: what a forged token is signed with."""
APPROVAL = "apr_edge"


def mint(
    environment: str,
    *,
    holder: str | None = ROHAN,
    scopes: tuple[str, ...] = ("claims:read", "claims:write"),
    audience: str | None = None,
    issuer: str | None = None,
    ttl_s: int = 300,
    key: LocalIssuer = SIGNER,
    workflow: bool = False,
) -> str:
    """A token for the claims system, as the agent sends it on this stack."""
    now = int(time.time())
    claims: dict[str, Any] = {"sub": "login-1", "iat": now, "exp": now + ttl_s}
    if holder is not None:
        claims[ident.CLAIM_CUSTOMER] = holder
    if environment == "azure":  # Entra: `uti`, `scp` a string, the workflow's app role
        claims |= {"iss": issuer or ENTRA_ISSUER, "aud": audience or CLAIMS_APP}
        claims |= {"uti": uuid.uuid4().hex, "azp": "agent-app"}
        if workflow:
            claims["roles"] = ["payouts.issue"]
        else:
            claims["scp"] = " ".join(scopes)
    else:  # the agent app's local issuer: `jti`, `scp` a list
        claims |= {"iss": issuer or LOCAL_ISSUER, "aud": audience or "claims-system"}
        claims |= {"jti": uuid.uuid4().hex, "azp": "claims-fnol"}
        claims["scp"] = ["claims:read", "payouts:write"] if workflow else list(scopes)
    return jwt.encode(claims, key._key, algorithm="RS256", headers={"kid": key.kid})


def granted(claim: str, amount: int) -> Any:
    """The agent's record of a handler's grant for paying `claim` (A3)."""
    now = int(time.time())
    return approval_record(
        Approval(
            id=APPROVAL,
            action="issue_payout",
            args={"claim_id": claim, "amount": str(amount)},
            reason="above the automatic limit",
            customer_id=ROHAN,
            idempotency_key="run_edge:2:1",
            created_at=now,
            expires_at=now + 3600,
            state=ApprovalState.DONE,
            decided_by="asha",
        )
    )


@asynccontextmanager
async def claims_system(environment: str) -> AsyncIterator[str]:
    """The claims system over HTTP, checking callers as `environment`'s overlay says."""
    async with throwaway("claims_edge") as url:
        await st.migrate(url)
        await st.seed(url, world_records())
        records = InMemoryRecords()
        await records.put_approval(granted("CLM-010004", 25001))
        keys = {"keys": ident.JWKS(SIGNER.jwks)}
        planned = adapters.plan(overlay(environment))
        env = pytest.MonkeyPatch()
        env.setenv("AZURE_TENANT_ID", TENANT)  # what the claims container is given
        env.setenv("CLAIMS_SYSTEM_APP_ID", CLAIMS_APP)
        async with st.Store.open(url) as store, authorisation(planned, hooks=keys) as authorise:
            env.undo()
            server = srv.build(store, authorise=authorise, approvals=records)
            config = uvicorn.Config(server.streamable_http_app(), port=0, log_level="warning")
            running = uvicorn.Server(config)
            task = asyncio.create_task(running.serve())
            while not running.started:
                await asyncio.sleep(0.02)
            port = running.servers[0].sockets[0].getsockname()[1]
            try:
                yield f"http://127.0.0.1:{port}/mcp"
            finally:
                running.should_exit = True
                await task


async def called(url: str, token: str | None, tool: str, args: dict[str, Any], **meta: Any) -> Any:
    """One call with `token` as its bearer header; the session asserts Rohan."""
    headers = {"authorization": f"Bearer {token}"} if token else {}
    sent = {srv.SESSION_META: {"customer_id": ROHAN}, **meta}
    async with (
        httpx2.AsyncClient(headers=headers, timeout=30) as http,
        Client(streamable_http_client(url, http_client=http)) as client,
    ):
        return await client.call_tool(tool, args, meta=sent)  # type: ignore[arg-type]


MISSING = ("get_claim", {"id": "CLM-999999"})
"""What a claim nobody holds reads as: what a stranger's must read as too."""
NAMED = {srv.APPROVAL_META: APPROVAL}

# (row, the token (None: none at all), tool, arguments, extra `_meta`, expected)
#   expected: "refused"; "as missing" (the answer a missing claim gets);
#   or the fields the answer must hold
ROWS: list[tuple[str, dict[str, Any] | None, str, dict[str, Any], dict[str, Any], Any]] = [
    (
        "a forged signature is refused",
        {"key": STRANGER},
        "get_claim",
        {"id": "CLM-010001"},
        {},
        "refused",
    ),
    (
        "a wrong audience is refused",
        {"audience": "claims-fnol"},
        "get_claim",
        {"id": "CLM-010001"},
        {},
        "refused",
    ),
    (
        "a wrong issuer is refused",
        {"issuer": "http://elsewhere.test/realms/claims"},
        "get_claim",
        {"id": "CLM-010001"},
        {},
        "refused",
    ),
    (
        "an expired token is refused",
        {"ttl_s": -60},
        "get_claim",
        {"id": "CLM-010001"},
        {},
        "refused",
    ),
    (
        "no token is refused, whatever the session asserts",
        None,
        "get_claim",
        {"id": "CLM-010001"},
        {},
        "refused",
    ),
    (
        "another policyholder's claim reads as a missing one",
        {},
        "get_claim",
        {"id": "CLM-019001"},
        {},
        "as missing",
    ),
    (
        "asserting Rohan does not make Meera's token his",
        {"holder": MEERA},
        "get_claim",
        {"id": "CLM-010001"},
        {},
        "as missing",
    ),
    (
        "a write without its scope is refused",
        {"scopes": ("claims:read",)},
        "register_claim",
        {"id": "POL-010001", "incident_type": "collision"},
        {},
        "refused",
    ),
    ("a policyholder cannot pay out", {}, "issue_payout", {"id": "CLM-010004"}, NAMED, "refused"),
    (
        "a valid read is served",
        {},
        "get_claim",
        {"id": "CLM-010001"},
        {},
        {"found": True, "status": "approved"},
    ),
    (
        "a valid write is served",
        {},
        "register_claim",
        {"id": "POL-010001", "incident_type": "collision"},
        {},
        {"created": "CLM-019002"},
    ),
    (
        "the payout workflow reads the claim its approval names",
        {"holder": None, "workflow": True},
        "get_claim",
        {"id": "CLM-010004"},
        NAMED,
        {"found": True, "approved_amount": 25001},
    ),
    (
        "the payout workflow reads no other claim on that approval",
        {"holder": None, "workflow": True},
        "get_claim",
        {"id": "CLM-010003"},
        NAMED,
        "as missing",
    ),
    (
        "the payout workflow with no approval reads nothing",
        {"holder": None, "workflow": True},
        "get_claim",
        {"id": "CLM-010004"},
        {},
        "as missing",
    ),
]


@pytest.mark.discharges("AAC-0057", "AAC-0106", "AHC-0099")
@pytest.mark.parametrize("environment", ["local", "azure"])
@pytest.mark.parametrize(
    ("row", "made", "tool", "args", "meta", "expected"), ROWS, ids=[r[0] for r in ROWS]
)
async def test_the_claims_system_checks_the_callers_token(
    environment: str,
    row: str,
    made: dict[str, Any] | None,
    tool: str,
    args: dict[str, Any],
    meta: dict[str, Any],
    expected: Any,
) -> None:
    async with claims_system(environment) as url:
        token = None if made is None else mint(environment, **made)
        said = await called(url, token, tool, args, **meta)
        missing = await called(url, mint(environment), *MISSING)
    if expected == "refused":
        assert said.is_error and "refused" in str(said.content), row
        return
    assert not said.is_error, (row, said.content)
    answer = said.structured_content
    if expected == "as missing":
        unknown = {
            k: str(v).replace(MISSING[1]["id"], args["id"])
            for k, v in missing.structured_content.items()
        }
        assert {k: str(v) for k, v in answer.items()} == unknown, row
        return
    assert all(str(v) in str(answer.get(k)) for k, v in expected.items()), (row, answer)


AZURE = overlay("azure")


@pytest.mark.discharges("AAC-0057", "AHC-0022")
def test_the_azure_overlay_cannot_bind_the_believing_adapter(tmp_path: Path) -> None:
    """The Azure overlay with `authorise: asserted` in place of `entra-id` is
    refused at startup; the overlay as written resolves to `entra-id`."""
    assert adapters.plan(AZURE).adapter("authorise") == "entra-id"
    text = AZURE.read_text()
    believing = text[: text.index("  authorise:")] + "  authorise:\n    adapter: asserted\n"
    copy = tmp_path / "azure.yaml"
    copy.write_text(
        believing.replace(
            "../../harness-profile.yaml", str(AZURE.parents[2] / "harness-profile.yaml")
        )
    )
    with pytest.raises(OverlayRefused, match="only a test overlay"):
        adapters.plan(copy)


OVERLAYS = [("local", "local-dev"), ("azure", "entra-id"), ("test", "asserted")]


@pytest.mark.discharges("AHC-0004")
@pytest.mark.parametrize(("name", "adapter"), OVERLAYS, ids=[o[0] for o in OVERLAYS])
def test_each_claims_overlay_binds_its_authorise_adapter(name: str, adapter: str) -> None:
    planned = adapters.plan(overlay(name))
    assert planned.environment == name
    assert set(planned.bound) == {"secrets", "config", "authorise"}
    assert planned.adapter("authorise") == adapter
