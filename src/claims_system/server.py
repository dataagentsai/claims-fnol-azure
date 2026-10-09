"""The claims system's own MCP server: the far end the agent talks to (Tier 2).

The same eight operations the AgentTwin world projects (`external.claims_system`
in the AOAS), with the same names, arguments, side-effect classes and answer
shapes — and behind them PostgreSQL (`claims_system.store`). Only the agent's
tool URL changes.

Two operations of the AOAS are not here, on purpose: `request_payout` is the
agent's own tool, which asks the approval wait (`routes_to: issue_payout`), and
`escalate` is the escalation wait. Neither is the claims system's; the AOAS's
`external.claims_system.operations` lists the eight below and no others.

**Who is asking** comes from the call's session (`aoas/session`), through the
`authorise` hook. Locally the hook believes the session the agent asserts, as
the AgentTwin world does; the Azure binding replaces it with a verified Entra
token (Tier 4). Whichever, ownership is decided here, by the row.

**Paying money needs a decision this system can read.** `issue_payout` must name
an approval (`aoas/approval`), which this system loads from where the approval
wait keeps it and checks covers *this* call: granted, unexpired, for this
operation, this policyholder, this claim and amount, this idempotency key, and
not decided by the policyholder. Above the automatic limit, the decision must be
a person's. The limit is restated here, not imported — the far end's own
statement of the rule (AOAS `issue_payout.authority`).

Each tool also declares its `entity`, so the agent's freshness re-read picks the
right reader without help from its binding (FINDINGS F-11).
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable, Mapping
from decimal import Decimal
from typing import Annotated, Any, Literal

from agent_harness.contracts import Approval
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

AUTOMATIC_LIMIT = Decimal("25000")
"""AOAS `issue_payout.authority.agent_when`: approved_amount at most ₹25,000."""
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

Authorise = Callable[[str, dict[str, Any], Mapping[str, object]], Awaitable[str | None]]
"""`async (operation, arguments, meta) -> policyholder id | None`; raise
`ToolError` to refuse the call."""

ApprovalLoader = Callable[[str], Awaitable[Approval | None]]
"""Load an approval record by id from wherever the approval wait keeps it."""

IncidentType = Literal["collision", "theft", "fire", "flood", "glass", "vandalism"]
ClaimRef = Annotated[str, Field(description="The claim's reference, CLM- and six digits.")]
PolicyRef = Annotated[str, Field(description="The policy's reference, POL- and six digits.")]


class NotAuthorised(ToolError):
    """This system will not act on the call; the caller reads why."""


async def asserted(
    operation: str, arguments: dict[str, Any], meta: Mapping[str, object]
) -> str | None:
    """The session the agent asserts, as the AgentTwin world believes it.

    The harness's MCP client sends `{customer_id}`; the AOAS session field is
    `policyholder_id` (FINDINGS F-5). Either is read. Only for a local run: a
    deployment verifies a token instead (Tier 4)."""
    del operation, arguments
    session = meta.get(SESSION_META)
    if not isinstance(session, dict):
        return None
    holder = session.get("policyholder_id") or session.get("customer_id")
    return str(holder) if holder else None


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
    approval: Approval | None,
    *,
    holder: str,
    claim: Row,
    key: str | None,
    now: float,
) -> str | None:
    """Why this approval does not cover paying this claim now, or `None`."""
    if approval is None:
        return "issue_payout needs an approval, and none this system can read was named"
    amount = Decimal(str(claim.get("approved_amount", 0)))
    named = {str(v) for v in approval.args.values()}
    reasons = [
        (not (approval.decided and approval.granted), "it was not granted"),
        (now >= approval.expires_at, "it has expired"),
        (approval.action != "issue_payout", f"it approved {approval.action}"),
        (approval.customer_id != holder, "it is for another policyholder"),
        (approval.decided_by in (None, holder), "nobody but the policyholder approved it"),
        (key != approval.idempotency_key, "it was requested as another call"),
        (str(claim["id"]) not in named, "it is for another claim"),
        (str(amount) not in named, f"it was not decided on the amount {amount}"),
        (
            amount > AUTOMATIC_LIMIT and approval.decided_by == AUTOMATIC_APPROVER,
            f"{amount} is above the automatic limit, and no person decided it",
        ),
    ]
    failed = [why for broken, why in reasons if broken]
    if not failed:
        return None
    return f"approval {approval.id} does not cover this payout: {'; '.join(failed)}"


def build(
    store: Store,
    *,
    authorise: Authorise = asserted,
    approvals: ApprovalLoader | None = None,
    clock: Callable[[], float] = time.time,
) -> MCPServer:
    """The store, as an MCP server that decides for itself whose rows these are."""
    server = MCPServer("claims_system")

    async def holder(operation: str, arguments: dict[str, Any], ctx: Context | None) -> str | None:
        return await authorise(operation, arguments, _meta(ctx))

    async def payable(claim_id: str, who: str, ctx: Context | None) -> None:
        """The far end's own check on money moving (AHC-0057)."""
        claim = await store.get_claim(who, claim_id)
        if not claim.get("found"):
            return  # answered as unknown by the store, never as refused
        meta = _meta(ctx)
        approval_id = meta.get(APPROVAL_META)
        loaded = (
            await approvals(approval_id)
            if approvals is not None and isinstance(approval_id, str)
            else None
        )
        why = covers(loaded, holder=who, claim=claim, key=_key(meta), now=clock())
        if why is not None:
            raise NotAuthorised(why)

    _reads(server, store, holder)
    _writes(server, store, holder, payable)
    return server


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


APPROVAL_RECORD = "approval"
"""The event the approval wait publishes its record under
(`agent_harness.approvals.dbos.RECORD`), restated: this system reads the wait's
record, it does not run the wait."""


def dbos_approvals(system_database_url: str) -> ApprovalLoader:
    """Approvals as the DBOS wait keeps them: its record event, read with a
    DBOS client and no DBOS runtime of this system's own."""
    from dbos import DBOSClient

    client = DBOSClient(system_database_url=system_database_url)

    async def load(approval_id: str) -> Approval | None:
        found = await client.get_event_async(approval_id, APPROVAL_RECORD, timeout_seconds=0)
        return found if isinstance(found, Approval) else None

    return load


__all__ = [
    "APPROVAL_META",
    "AUTOMATIC_APPROVER",
    "AUTOMATIC_LIMIT",
    "IDEMPOTENCY_META",
    "SCOPES",
    "SESSION_META",
    "ApprovalLoader",
    "Authorise",
    "NotAuthorised",
    "asserted",
    "build",
    "covers",
    "dbos_approvals",
]
