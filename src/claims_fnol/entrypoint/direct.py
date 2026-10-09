"""The deterministic route: claim, payout and policy status, with no model call (P-DIRECT).

A registry keyed by the handler name the router chose. Every handler reads one
row from the claims system and renders it by template — never a date, never a
prediction (P-CLAIM-STATUS) — and returns a typed result on every path.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from agent_harness import context as ctx
from agent_harness import telemetry as tel

from claims_fnol import binding
from claims_fnol.contracts import (
    Completed,
    Direct,
    Failed,
    IdempotencyKey,
    Identity,
    RunId,
    ToolClient,
    ToolUnavailable,
    TurnResult,
    bind_arguments,
)

LOOKUP_TOOLS: Mapping[str, str] = {
    "claim_status": "get_claim",
    "payout_status": "get_claim",
    "policy_status": "get_policy",
}
"""Which read answers each handler — what the turn records as read (AHC-0108)."""

NOT_FOUND_REPLY = "I could not find {{ ref }} among your policies and claims."
"""What `found: false` is told. The claims system answers it alike for a record
that does not exist and one that is somebody else's, so the reply names neither."""

CLAIM_REPLIES: Mapping[str, str] = {
    "registered": (
        "Claim {{ id }} is registered. An assessor will look at it and tell you what "
        "they need next."
    ),
    "documents_pending": (
        "Claim {{ id }} is waiting on documents: the assessor still needs "
        "{{ documents_missing }} {{ 'document' if documents_missing == 1 else 'documents' }} "
        "from you. You can send them here."
    ),
    "under_assessment": (
        "Claim {{ id }} is under assessment. The assessor decides it; I cannot say what "
        "they will decide."
    ),
    "approved": (
        "Claim {{ id }} is approved, for ₹{{ amount }}, and has not been paid yet. If you "
        "would like it paid, ask me to release the payment."
    ),
    "rejected": "Claim {{ id }} is rejected. A colleague can talk you through the decision.",
    "paid": "Claim {{ id }} is paid: ₹{{ amount }} went to the account on your policy.",
    "withdrawn": "Claim {{ id }} is withdrawn.",
}

PAYOUT_REPLIES: Mapping[str, str] = {
    "approved": (
        "Claim {{ id }} is approved, for ₹{{ amount }}, and the payment has not been made "
        "yet. If you would like it paid, ask me to release the payment."
    ),
    "paid": "Claim {{ id }} is paid: ₹{{ amount }} went to the account on your policy.",
}
NO_PAYOUT_REPLY = (
    "Claim {{ id }} is {{ status_words }}, so there is no payment on it yet. A claim is "
    "paid once it is approved."
)

POLICY_REPLY = (
    "Policy {{ id }} for {{ vehicle_reg }} is {{ status }}. It is a {{ product_words }} "
    "policy with an excess of ₹{{ excess }}."
)


class DirectHandler(Protocol):
    async def __call__(
        self, decision: Direct, identity: Identity, run_id: RunId, tools: ToolClient
    ) -> TurnResult: ...


async def _row(
    decision: Direct, identity: Identity, run_id: RunId, tools: ToolClient
) -> dict[str, object] | Completed | Failed:
    """The row the router found, from the claims system — or that it was not
    found, or a typed failure. The argument name is read from the tool's schema."""
    tool = LOOKUP_TOOLS[decision.handler]
    key = IdempotencyKey(run_id=run_id, step=0, iteration=0)
    try:
        spec = (await tools.list_tools(identity)).get(tool)
        if spec is None:
            return Failed(
                customer_message="I cannot look that up right now.",
                detail=f"{tool} is not on this identity's surface",
            )
        result = await tools.call(tool, bind_arguments(spec, decision.args), identity, key)
    except ToolUnavailable as exc:
        return Failed(customer_message=binding.UNREACHABLE, detail=str(exc))
    except Exception as exc:  # noqa: BLE001 — the contract holds here too
        return Failed(
            customer_message="I could not look that up.", detail=f"{type(exc).__name__}: {exc}"
        )
    if result.is_error or not isinstance(result.structured, dict):
        return Failed(
            customer_message="I could not look that up.",
            detail=result.text or "no structured content",
        )
    if result.structured.get("found") is False:
        return Completed(reply=ctx.render(NOT_FOUND_REPLY, ref=decision.args.get("id", "")))
    return result.structured


def _money(value: object) -> str:
    try:
        return f"{int(str(value)):,}"
    except ValueError:
        return str(value)


def _words(row: Mapping[str, object]) -> dict[str, object]:
    return {
        **row,
        "amount": _money(row.get("approved_amount")),
        "excess": _money(row.get("excess")),
        "status_words": str(row.get("status", "")).replace("_", " "),
        "product_words": str(row.get("product", "")).replace("_", "-"),
    }


async def claim_status(
    decision: Direct, identity: Identity, run_id: RunId, tools: ToolClient
) -> TurnResult:
    """P-CLAIM-STATUS: the claim's status, and what it says — missing documents,
    the approved amount — from the claim, by template."""
    row = await _row(decision, identity, run_id, tools)
    if not isinstance(row, dict):
        return row
    template = CLAIM_REPLIES.get(str(row.get("status")), "Claim {{ id }} is {{ status_words }}.")
    return Completed(reply=ctx.render(template, **_words(row)))


async def payout_status(
    decision: Direct, identity: Identity, run_id: RunId, tools: ToolClient
) -> TurnResult:
    """P-PAYOUT-OWED: a question about payout status states the status and issues nothing."""
    row = await _row(decision, identity, run_id, tools)
    if not isinstance(row, dict):
        return row
    template = PAYOUT_REPLIES.get(str(row.get("status")), NO_PAYOUT_REPLY)
    return Completed(reply=ctx.render(template, **_words(row)))


async def policy_status(
    decision: Direct, identity: Identity, run_id: RunId, tools: ToolClient
) -> TurnResult:
    """Whether a policy is in force, and what it is — never whether it covers anything."""
    row = await _row(decision, identity, run_id, tools)
    if not isinstance(row, dict):
        return row
    return Completed(reply=ctx.render(POLICY_REPLY, **_words(row)))


HANDLERS: Mapping[str, DirectHandler] = {
    "claim_status": claim_status,
    "payout_status": payout_status,
    "policy_status": policy_status,
}


async def answer(
    decision: Direct,
    identity: Identity,
    run_id: RunId,
    tools: ToolClient,
    handlers: Mapping[str, DirectHandler] = HANDLERS,
) -> TurnResult:
    """Dispatch to the handler the router named. No model call, and the trace says so."""
    with tel.span("agent.direct", **{"agent.handler": decision.handler}):
        handler = handlers.get(decision.handler)
        if handler is None:
            return Failed(
                customer_message="I cannot look that up right now.",
                detail=f"no deterministic handler named {decision.handler!r}",
            )
        return await handler(decision, identity, run_id, tools)


__all__ = [
    "CLAIM_REPLIES",
    "HANDLERS",
    "LOOKUP_TOOLS",
    "NOT_FOUND_REPLY",
    "PAYOUT_REPLIES",
    "POLICY_REPLY",
    "DirectHandler",
    "answer",
    "claim_status",
    "payout_status",
    "policy_status",
]
