"""The claims system's own MCP server: the far end the agent talks to (Tier 2).

The same eight operations the AgentTwin world projects (`external.claims_system`
in the AOAS), with the same names, arguments, side-effect classes and answer
shapes — and behind them PostgreSQL (`claims_system.store`). Only the agent's
tool URL changes.

Two operations of the AOAS are not here, on purpose: `request_payout` is the
agent's own tool, which asks the approval wait (`routes_to: issue_payout`), and
`escalate` is the escalation wait. Neither is the claims system's; the AOAS's
`external.claims_system.operations` lists the eight below and no others.

**Who is asking** is decided by the `authorise` port (`agent_harness.identity.
far_end`, A1), bound by this system's own overlay (`config/claims-system/`):
the bearer token the call carried — the `Authorization` header over HTTP, the
session's `token` when the client is in this process — verified for this
system's audience, the policyholder read from its claim, and the operation's
scope checked against what the token grants. Never the agent's word: only a
test overlay may bind `asserted`, which believes it, and the registry refuses
it anywhere else. Whichever, ownership is decided here, by the row.

**The payout workflow has no policyholder's session** when a handler approves
an hour later. It calls under its own login: a token with no holder that holds
`payouts:write`. For that caller alone, and only on a call that names an
approval, the approval record says whose claim it is — and only for the claim
that record is about; everything else it asks reads as unknown.

**Paying money needs a decision this system can read.** `issue_payout` must name
an approval (`aoas/approval`). This system reads the agent's own record of it
through the harness's records port (`ApprovalRecordReader`, A3) on a read-only
connection of its own — never the wait's engine, so DBOS's storage format is not
on the money path — and checks it covers *this* call: granted and unexpired, and
its `args_digest` equal to the one rebuilt from the call as received (this
operation, this claim, the amount this system holds, the policyholder it
verified, the idempotency key on the call); and not decided by the
policyholder. Above the automatic limit, the decision must be a person's. The
limit is restated here, not imported — the far end's own statement of the rule
(AOAS `issue_payout.authority`).

**The limit is read through this system's own config port** (A6), from its own
overlay (`config/claims-system/<env>.yaml`), once per payout. One source of
truth: the same App Configuration key and label the agent reads,
`payout.automatic_limit_inr` under `dev`, read by this system's own identity
(App Configuration Data Reader, never a writer), never by asking the agent.
Two keys would need two changes to move one rule, and a gap between them is
either a payout the agent granted and this check refuses or one this check
would let through that the agent sent to a person. With one key the two can
differ only while one side's cache is older than the other's (30 s). This check
is the money wall either way: the key's default and ceiling are the AOAS's, so
no value raises this system above the AOAS without a spec change; and wherever
the agent's limit and this one differ, the lower wins — the agent sends a
payout above its own limit to a person, and this check refuses an automatic
grant above its own (`tests/test_payout_limit_live.py`; FINDINGS F-71).

Each tool also declares its `entity`, so the agent's freshness re-read picks the
right reader without help from its binding (FINDINGS F-11).
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Annotated, Any, Literal

from agent_harness.config.settings import Key, Settings, between, defaults
from agent_harness.contracts.records import ApprovalRecord, ApprovalRecordReader, refusals
from agent_harness.identity.far_end import Authorise, Call, Caller, CallRefused
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from claims_system.store import Row, Store

SESSION_META = "aoas/session"
IDEMPOTENCY_META = "aoas/idempotency-key"
APPROVAL_META = "aoas/approval"
"""The binding's metadata keys, declared on this side too: the far end does not
import the agent's transport to learn where its caller put things."""

META_SIDE_EFFECT = "side_effect"
META_ENTITY = "entity"
META_REQUIRED_SCOPE = "required_scope"

SCOPES = {
    "register_claim": "claims:write",
    "submit_document": "claims:write",
    "withdraw_claim": "claims:write",
    "issue_payout": "payouts:write",
}
"""What this system requires of a caller, stated by this system. The agent's
binding has its own copy and filters its tool surface by it; a test holds the
two equal."""

READ_SCOPE = "claims:read"
"""What a read requires of a verified caller. Not advertised per tool, as the
agent's binding has no read scopes to filter by; every caller it sends holds it."""

WORKFLOW_SCOPE = "payouts:write"
"""What makes a caller with no holder the payout workflow's own login."""

AOAS_LIMIT = Decimal("25000")
"""AOAS `issue_payout.authority.agent_when`: approved_amount at most ₹25,000."""
AUTOMATIC_LIMIT = Key(
    "payout.automatic_limit_inr", Decimal, AOAS_LIMIT, check=between(1, AOAS_LIMIT)
)
"""The same key the agent reads (one source of truth); the AOAS's number its
default and its ceiling."""
KEYS = (AUTOMATIC_LIMIT,)
AUTOMATIC_APPROVER = "policy:automatic-limit"
"""Who grants a payout within the limit, as the approval records it."""

DESCRIPTIONS = {
    "get_policy": "Look up one motor policy — its product, status, vehicle registration "
    "and excess.",
    "list_policies": "List the signed-in policyholder's motor policies.",
    "get_claim": "Look up one claim — its status, incident type, documents still missing "
    "and any approved amount.",
    "list_claims": "List the signed-in policyholder's claims.",
    "register_claim": "Register a new incident on an active policy (first notice of loss). "
    "`id` is the policy. Decides nothing about cover. Returns the new claim's reference "
    "under `created`.",
    "submit_document": "Attach a document the assessor asked for. Only while the claim is "
    "registered or waiting on documents.",
    "withdraw_claim": "Withdraw a claim before its assessment starts. Cannot be undone.",
    "issue_payout": "Pay an approved claim its approved amount to the account on the "
    "policy. Cannot be undone.",
}
"""The claims system's own words (the world's `presents`)."""

IncidentType = Literal["collision", "theft", "fire", "flood", "glass", "vandalism"]
ClaimRef = Annotated[str, Field(description="The claim's reference, CLM- and six digits.")]
PolicyRef = Annotated[str, Field(description="The policy's reference, POL- and six digits.")]


class NotAuthorised(ToolError):
    """This system will not act on the call; the caller reads why."""


def _meta(ctx: Context | None) -> dict[str, object]:
    """The call's metadata, or empty — never a guess."""
    try:
        meta = ctx.request_context.meta if ctx is not None else None
    except (AttributeError, ValueError):
        return {}
    if meta is None:
        return {}
    if isinstance(meta, dict):
        return meta
    return dict(getattr(meta, "model_extra", None) or {})


def _bearer(ctx: Context | None, meta: Mapping[str, object]) -> str | None:
    """The token the call carried: its HTTP request's `Authorization` header,
    or, with no HTTP request (a client in this process), the session's token.
    Over HTTP the body is never read for one."""
    try:
        request = ctx.request_context.request if ctx is not None else None
    except (AttributeError, ValueError):
        request = None
    if request is not None:
        header = str(request.headers.get("authorization", ""))
        scheme, _, token = header.partition(" ")
        if scheme.lower() != "bearer" or not token.strip():
            return None
        return token.strip()
    session = meta.get(SESSION_META)
    token = session.get("token") if isinstance(session, dict) else None
    return token if isinstance(token, str) and token else None


def _key(meta: Mapping[str, object]) -> str | None:
    key = meta.get(IDEMPOTENCY_META)
    return key if isinstance(key, str) and key else None


def _tool_meta(operation: str, side_effect: str, entity: str) -> dict[str, str]:
    scope = SCOPES.get(operation)
    return {
        META_SIDE_EFFECT: side_effect,
        META_ENTITY: entity,
        **({META_REQUIRED_SCOPE: scope} if scope else {}),
    }


def covers(
    approval: ApprovalRecord | None,
    *,
    holder: str,
    claim: Row,
    key: str | None,
    now: float,
    limit: Decimal = AOAS_LIMIT,
) -> str | None:
    """Why this approval record does not cover paying this claim now, or `None`.

    The call is rebuilt as this system received it — the claim, the amount this
    system holds for it, the policyholder it verified, the key on the call — and
    its digest must be the record's (`agent_harness.contracts.records.refusals`).
    `limit` is the automatic limit read for this payout."""
    if approval is None:
        return "issue_payout needs an approval, and none this system can read was named"
    amount = Decimal(str(claim.get("approved_amount", 0)))
    failed = refusals(
        approval,
        action="issue_payout",
        args={"claim_id": str(claim["id"]), "amount": str(amount)},
        requested_for=holder,
        idempotency_key=key or "",
        now=now,
    )
    if approval.decided_by in (None, holder):
        failed.append("nobody but the policyholder approved it")
    if amount > limit and approval.decided_by == AUTOMATIC_APPROVER:
        failed.append(f"{amount} is above the {limit} automatic limit, and no person decided it")
    if not failed:
        return None
    return f"approval {approval.id} does not cover this payout: {'; '.join(failed)}"


@dataclass(frozen=True)
class _Gate:
    """Who is asking, and whether money may move: this system's checks."""

    store: Store
    authorise: Authorise
    approvals: ApprovalRecordReader | None
    clock: Callable[[], float]
    limits: Settings

    async def record(self, meta: Mapping[str, object]) -> ApprovalRecord | None:
        named = meta.get(APPROVAL_META)
        if self.approvals is None or not isinstance(named, str):
            return None
        return await self.approvals.approval(named)

    async def holder(
        self, operation: str, arguments: dict[str, Any], ctx: Context | None
    ) -> str | None:
        meta = _meta(ctx)
        call = Call(operation, SCOPES.get(operation, READ_SCOPE), _bearer(ctx, meta), meta)
        try:
            caller = await self.authorise(call)
        except CallRefused as exc:
            raise NotAuthorised(f"{operation} refused: {exc}") from None
        if caller.holder is not None or not _workflow(caller):
            return caller.holder
        return _on_approval(await self.record(meta), operation, arguments)

    async def payable(self, claim_id: str, who: str, ctx: Context | None) -> None:
        """The far end's own check on money moving (AHC-0057)."""
        claim = await self.store.get_claim(who, claim_id)
        if not claim.get("found"):
            return  # answered as unknown by the store, never as refused
        meta = _meta(ctx)
        approval = await self.record(meta)
        limit = self.limits.get(AUTOMATIC_LIMIT)  # once per payout (A6)
        why = covers(
            approval, holder=who, claim=claim, key=_key(meta), now=self.clock(), limit=limit
        )
        if why is not None:
            raise NotAuthorised(why)


def build(
    store: Store,
    *,
    authorise: Authorise,
    approvals: ApprovalRecordReader | None = None,
    clock: Callable[[], float] = time.time,
    limits: Settings | None = None,
) -> MCPServer:
    """The store, as an MCP server that decides for itself whose rows these are.
    `limits` is this system's config port; without one, the declared defaults."""
    server = MCPServer("claims_system")
    gate = _Gate(store, authorise, approvals, clock, limits or defaults(*KEYS))
    _reads(server, store, gate.holder)
    _writes(server, store, gate.holder, gate.payable)
    return server


def _workflow(caller: Caller) -> bool:
    """The payout workflow's own login: no holder, and `payouts:write`."""
    return caller.holder is None and WORKFLOW_SCOPE in caller.scopes


def _on_approval(
    approval: ApprovalRecord | None, operation: str, arguments: dict[str, Any]
) -> str | None:
    """Whose claim the workflow may touch: the named approval's policyholder,
    for the one claim that approval is about, and only to read it or pay it."""
    if approval is None or operation not in ("get_claim", "issue_payout"):
        return None
    if approval.args.get("claim_id") != arguments.get("id"):
        return None
    return approval.requested_for


Holder = Callable[[str, dict[str, Any], Context | None], Awaitable[str | None]]


def _reads(server: MCPServer, store: Store, holder: Holder) -> None:
    def tool(name: str, entity: str) -> Callable[[Any], Any]:
        return server.tool(
            name=name,
            description=DESCRIPTIONS[name],
            meta=_tool_meta(name, "read", entity),
            structured_output=True,
        )

    @tool("get_policy", "policy")
    async def get_policy(id: PolicyRef, ctx: Context | None = None) -> dict[str, Any]:
        return await store.get_policy(await holder("get_policy", {"id": id}, ctx), id)

    @tool("list_policies", "policy")
    async def list_policies(ctx: Context | None = None) -> dict[str, Any]:
        return await store.list_policies(await holder("list_policies", {}, ctx))

    @tool("get_claim", "claim")
    async def get_claim(id: ClaimRef, ctx: Context | None = None) -> dict[str, Any]:
        return await store.get_claim(await holder("get_claim", {"id": id}, ctx), id)

    @tool("list_claims", "claim")
    async def list_claims(ctx: Context | None = None) -> dict[str, Any]:
        return await store.list_claims(await holder("list_claims", {}, ctx))


Payable = Callable[[str, str, Context | None], Awaitable[None]]


def _writes(server: MCPServer, store: Store, holder: Holder, payable: Payable) -> None:
    def tool(name: str, side_effect: str, entity: str) -> Callable[[Any], Any]:
        return server.tool(
            name=name,
            description=DESCRIPTIONS[name],
            meta=_tool_meta(name, side_effect, entity),
            structured_output=True,
        )

    @tool("register_claim", "reversible", "policy")
    async def register_claim(
        id: PolicyRef, incident_type: IncidentType, ctx: Context | None = None
    ) -> dict[str, Any]:
        who = await holder("register_claim", {"id": id, "incident_type": incident_type}, ctx)
        return await store.register_claim(who, id, incident_type, key=_key(_meta(ctx)))

    @tool("submit_document", "reversible", "claim")
    async def submit_document(
        id: ClaimRef,
        document_type: Annotated[
            str,
            Field(description="police report, FIR copy, repair estimate, photos or licence"),
        ],
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        who = await holder("submit_document", {"id": id, "document_type": document_type}, ctx)
        return await store.submit_document(who, id, document_type, key=_key(_meta(ctx)))

    @tool("withdraw_claim", "irreversible", "claim")
    async def withdraw_claim(id: ClaimRef, ctx: Context | None = None) -> dict[str, Any]:
        who = await holder("withdraw_claim", {"id": id}, ctx)
        return await store.withdraw_claim(who, id, key=_key(_meta(ctx)))

    @tool("issue_payout", "irreversible", "claim")
    async def issue_payout(id: ClaimRef, ctx: Context | None = None) -> dict[str, Any]:
        who = await holder("issue_payout", {"id": id}, ctx)
        if who is not None:
            await payable(id, who, ctx)
        return await store.issue_payout(who, id, key=_key(_meta(ctx)))


__all__ = [
    "APPROVAL_META",
    "AUTOMATIC_APPROVER",
    "AOAS_LIMIT",
    "AUTOMATIC_LIMIT",
    "KEYS",
    "IDEMPOTENCY_META",
    "READ_SCOPE",
    "SCOPES",
    "SESSION_META",
    "WORKFLOW_SCOPE",
    "NotAuthorised",
    "build",
    "covers",
]
