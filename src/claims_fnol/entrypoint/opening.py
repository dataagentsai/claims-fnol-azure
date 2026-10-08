"""What a policyholder is shown on opening a conversation: P-OPEN.

Their own open claims and policies, read through the claims system (scoped by
the session there, not here), and their work in flight — with no model call.
"""

from __future__ import annotations

from typing import Any

from agent_harness import telemetry as tel

from claims_fnol.contracts import (
    Approvals,
    Escalations,
    IdempotencyKey,
    Identity,
    RunId,
    ToolClient,
)

LIST_CLAIMS = "list_claims"
LIST_POLICIES = "list_policies"
CLOSED = frozenset({"rejected", "paid", "withdrawn"})
"""AOAS `claim_status.terminal`: a closed claim is not shown as open."""


async def opening(
    identity: Identity,
    *,
    tools: ToolClient,
    approvals: Approvals | None,
    escalations: Escalations | None,
) -> str:
    with tel.span("agent.opening", **{tel.TENANT: identity.customer_id}) as span:
        claims = await _listed(tools, identity, LIST_CLAIMS)
        policies = await _listed(tools, identity, LIST_POLICIES)
        waiting = await _in_flight(identity.customer_id, approvals, escalations)
        span.set_attribute("agent.opening.claims", -1 if claims is None else len(claims))
        span.set_attribute("agent.opening.in_flight", len(waiting))
    return _words(claims, policies, waiting)


async def _listed(tools: ToolClient, identity: Identity, tool: str) -> list[dict[str, Any]] | None:
    key = IdempotencyKey(run_id=RunId("opening"), step=0, iteration=0)
    result = await tools.call(tool, {}, identity, key)
    structured = None if result.is_error else result.structured
    items = structured.get("items") if isinstance(structured, dict) else None
    return [i for i in items if isinstance(i, dict)] if isinstance(items, list) else None


async def _in_flight(
    customer_id: str, approvals: Approvals | None, escalations: Escalations | None
) -> list[str]:
    waiting: list[str] = []
    for approval in await approvals.pending() if approvals is not None else ():
        if approval.customer_id == customer_id:
            about = approval.args.get("claim_id") or "a claim"
            waiting.append(f"the payout of {about}, waiting for a claims handler to authorise")
    for escalation in await escalations.pending() if escalations is not None else ():
        if escalation.customer_id == customer_id:
            waiting.append(f"{escalation.id}, with a colleague")
    return waiting


def _words(
    claims: list[dict[str, Any]] | None,
    policies: list[dict[str, Any]] | None,
    waiting: list[str],
) -> str:
    lines = ["Hello."]
    if claims is None or policies is None:
        lines.append(
            "I cannot see your claims right now, but you can ask about one by its reference."
        )
    else:
        open_claims = [c for c in claims if c.get("status") not in CLOSED]
        lines.append("Your open claims:" if open_claims else "You have no open claims.")
        lines.extend(
            f"- {c.get('id')}: {str(c.get('status')).replace('_', ' ')}" for c in open_claims
        )
        active = [p for p in policies if p.get("status") == "active"]
        if active:
            lines.append("Your active policies:")
            lines.extend(f"- {p.get('id')}: {p.get('vehicle_reg')}" for p in active)
    if waiting:
        lines.append("Already in hand:")
        lines.extend(f"- {w}" for w in waiting)
    lines.append("What can I help with?")
    return "\n".join(lines)


__all__ = ["LIST_CLAIMS", "LIST_POLICIES", "opening"]
