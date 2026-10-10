"""The payout request tool — the one action this agent may have to ask a person about —
and the two activities the approval workflow runs for a payout.

AOAS `request_payout` (offered, `routes_to: issue_payout`) and `issue_payout`
(`offered: false`, `amount_from: claim.approved_amount`). The tool is the
agent's side: it asks, and reports what the workflow answered. `PayoutWork` is
the workflow's side: it reads the claim, applies the policy, and issues a
granted payout under the approvals worker's own login (T-028).

This module is the clothing agent's `approvals/refund.py` with the nouns
changed: the shape — ask the harness's approval workflow, assess against the
far end's own row, carry out under a grant, re-read before spending it, read
the far end's `kind` — is mechanism the library does not yet hold (FINDINGS F-2).
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Literal

from agent_harness import telemetry as tel
from agent_harness.approvals.durable import ASSESS, CARRY_OUT, Ask, Assessment, CarriedOut
from agent_harness.approvals.workflow import ApprovalError, carry_out, moved, stored_key
from temporalio import activity

from claims_fnol.approvals.policy import (
    PAYOUT_ACTION,
    Policy,
    judged,
    not_requestable,
    requires_approval,
)
from claims_fnol.contracts import (
    ActionDeclined,
    Approval,
    ApprovalRequested,
    Approvals,
    ApprovalState,
    IdempotencyKey,
    Identity,
    LocalTool,
    SideEffectClass,
    ToolClient,
    ToolResult,
    ToolSpec,
    ToolUnavailable,
    Unbindable,
    bind_arguments,
)

PAYOUT_WAIT_REPLY = (
    "I have sent this payout to a claims handler to authorise, because it is above the "
    "amount I can release myself. Nothing has been paid yet."
)
"""P-APPROVAL-WAIT: a request exists, and nothing more — no date."""


class PayoutRequested(ApprovalRequested):
    """A payout that needs a claims handler. The loop sees only `ApprovalRequested`."""

    def __init__(self, approval: Approval) -> None:
        super().__init__(approval, PAYOUT_WAIT_REPLY)


REQUEST_PAYOUT = "request_payout"
CLAIM_LOOKUP = "get_claim"
POLICY_APPROVER = "policy:automatic-limit"
LIMIT_ATTRIBUTE = "agent.payout.automatic_limit_inr"
"""The limit a payout decision used, on its `agent.approval.assess` span (A6)."""
"""Who granted a payout within the limit, so "who let this money move?" has one
answer for both paths."""

REQUEST_PAYOUT_SPEC = ToolSpec(
    name=REQUEST_PAYOUT,
    description=(
        "Request the payout of an approved claim, for its approved amount, to the account on "
        "the policy. Payouts within the automatic limit are made at once; larger ones go to a "
        "claims handler to authorise, and you will not be told the outcome in this "
        "conversation. Tell the policyholder a payment was made only if this tool says so."
    ),
    input_schema={
        "type": "object",
        "properties": {"id": {"type": "string"}},
        "required": ["id"],
        "additionalProperties": False,
    },
    output_schema={
        "type": "object",
        "properties": {"status": {"type": "string"}, "approval_id": {"type": "string"}},
        "required": ["status"],
    },
    side_effect=SideEffectClass.REVERSIBLE,
)
"""Harness-local. **No amount input**: the amount is the claim's approved amount,
read from the claims system (`amount_from`)."""

DECLINED_REPLY = (
    "The payment for claim {claim_id} could not be made to the account on your policy, "
    "which can no longer receive it."
)
"""P-PAYOUT-DECLINED: said first, before the handoff names who takes it on."""


def payout_tool(
    approvals: Approvals,
    *,
    identity: Identity,
    idempotency_key: IdempotencyKey,
    conversation_id: str = "",
) -> LocalTool:
    """Bind the request tool to one run's identity and key, minted here so the
    model's arguments cannot choose the key the payout runs under."""

    async def handle(arguments: dict[str, object]) -> ToolResult:
        claim_id = arguments.get("id")
        if not isinstance(claim_id, str) or not claim_id:
            return _error(REQUEST_PAYOUT, "a claim reference is required")
        approval = await approvals.request(
            action=PAYOUT_ACTION,
            args={"claim_id": claim_id},
            identity=identity,
            idempotency_key=idempotency_key,
            conversation_id=conversation_id,
        )
        if approval.state is ApprovalState.WAITING:
            raise PayoutRequested(approval)
        if approval.state is ApprovalState.DONE:
            # Named by its row, as every claims-system answer is: this answer is
            # the latest word on the claim's status (AOAS `consistency`, F-21).
            done = {
                "id": claim_id,
                "status": "paid",
                "approval_id": approval.id,
                "claim_id": claim_id,
                "amount": str(approval.args.get("amount", "")),
            }
            return ToolResult(name=PAYOUT_ACTION, text=approval.result or "", structured=done)
        if approval.declined:
            raise ActionDeclined(DECLINED_REPLY.format(claim_id=claim_id), approval.result or "")
        return _error(REQUEST_PAYOUT, approval.result or f"the payout is {approval.state.value}")

    return LocalTool(spec=REQUEST_PAYOUT_SPEC, handler=handle)


def _outcome_of(result: ToolResult) -> CarriedOut:
    """What the far end's answer to a payout means, read from its `kind`
    (AOAS `refusal_is_a_result`): `transient` or a protocol error is raised so
    the workflow retries under the same key (AHC-0043); `declined` is final and
    named (P-PAYOUT-DECLINED); any other refusal is the far end's own reason."""
    said = result.structured if isinstance(result.structured, dict) else {}
    kind = said.get("kind")
    if (result.is_error and result.error_channel == "protocol") or kind == "transient":
        raise ToolUnavailable(result.text or str(said.get("reason", "")))
    if result.is_error or said.get("allowed") is False:
        reason = str(said.get("reason") or result.text)
        return CarriedOut(ok=False, declined=kind == "declined", text=reason)
    return CarriedOut(ok=True, text=result.text)


def _error(name: str, text: str) -> ToolResult:
    return ToolResult(name=name, text=text, is_error=True, error_channel="execution")


@dataclass(frozen=True)
class PayoutWork:
    """The payout's activities, run by the approvals worker under its own login."""

    tools: ToolClient
    acting_for: Callable[[str], Awaitable[Identity]]
    policy: Policy = field(default_factory=Policy)

    @activity.defn(name=ASSESS)
    async def assess(self, ask: Ask) -> Assessment:
        who = (await self.acting_for(ask.customer_id)).model_copy(update={"grant": ask.id})
        key = stored_key(_keyed(ask))
        claim = await _claim(self.tools, ask.args.get("claim_id"), who, key)
        if not isinstance(claim, dict):
            return Assessment(args=ask.args, reason=None, failed=claim.text)
        amount = claim.get("approved_amount")
        args = {**ask.args, "amount": None if amount is None else str(amount)}
        refused = not_requestable(claim, self.policy)
        if refused is not None:
            return Assessment(args=args, reason=None, failed=refused)
        # One read of the limit per decision (A6, AHC-0003), recorded on the
        # decision's span: a change in App Configuration applies to the next
        # payout, and each decision shows which limit it used.
        limit = self.policy.automatic_limit()
        attributes = {"agent.approval.id": ask.id, LIMIT_ATTRIBUTE: str(limit)}
        with tel.span("agent.approval.assess", **attributes):
            reason = requires_approval(claim, limit)
        return Assessment(
            args=args, reason=reason, approver=POLICY_APPROVER, decided_against=judged(claim)
        )

    @activity.defn(name=CARRY_OUT)
    async def carry_out(self, approval: Approval) -> CarriedOut:
        who = await self.acting_for(approval.customer_id)
        now = _attempted_at()
        with tel.span("agent.approval.carry_out", **{"agent.approval.id": approval.id}):
            changed = await self._changed(approval, who)
            if changed is not None:
                return CarriedOut(ok=False, stale=True, text=changed)
            try:
                result = await carry_out(approval, who, self.tools, policy=self.policy, now=now)
            except ApprovalError as exc:
                return CarriedOut(ok=False, text=str(exc))
        return _outcome_of(result)

    async def _changed(self, approval: Approval, who: Identity) -> str | None:
        """Re-read the claim before the grant is spent: what moved since it was
        assessed, if anything (P-APPROVAL-STALE)."""
        reading = who.model_copy(update={"grant": approval.id})
        claim = await _claim(
            self.tools, approval.args.get("claim_id"), reading, stored_key(approval)
        )
        if not isinstance(claim, dict):
            if claim.error_channel == "protocol":
                raise ToolUnavailable(claim.text)
            return f"the claim could not be read again: {claim.text}"
        return moved(approval, judged(claim))


def _attempted_at() -> int:
    """When this attempt was scheduled, on Temporal; the wall's, on DBOS.

    The same activity runs as a DBOS step (Tier 2), where there is no activity
    context to ask: the step reads the wall once and its result is checkpointed,
    which is `box.now`'s guarantee for this one call."""
    if activity.in_activity():
        return int(activity.info().current_attempt_scheduled_time.timestamp())
    return int(time.time())


def _keyed(ask: Ask) -> Approval:
    return Approval(
        id=ask.id,
        action=ask.action,
        reason="",
        customer_id=ask.customer_id,
        idempotency_key=ask.idempotency_key,
        created_at=0,
        expires_at=0,
    )


async def _claim(
    tools: ToolClient, claim_id: object, identity: Identity, key: IdempotencyKey
) -> dict[str, object] | ToolResult:
    """The claim as the claims system holds it, read as the policyholder (P-OWNERSHIP)."""

    def unread(text: str, channel: Literal["execution", "protocol"]) -> ToolResult:
        return ToolResult(name=REQUEST_PAYOUT, text=text, is_error=True, error_channel=channel)

    if not isinstance(claim_id, str) or not claim_id:
        return unread("a claim reference is required", "execution")
    try:
        spec = (await tools.list_tools(identity)).get(CLAIM_LOOKUP)
        if spec is None:
            return unread(f"{CLAIM_LOOKUP} is not on this identity's surface", "protocol")
        arguments = bind_arguments(spec, {"claim_id": claim_id})
        result = await tools.call(CLAIM_LOOKUP, arguments, identity, key)
    except (ToolUnavailable, Unbindable) as exc:
        return unread(str(exc), "protocol")
    if result.is_error or not isinstance(result.structured, dict):
        return unread(result.text or "that claim could not be found", "execution")
    if result.structured.get("found") is False:
        return unread(f"no claim {claim_id} on this policyholder's policies", "execution")
    return result.structured


__all__ = [
    "CLAIM_LOOKUP",
    "DECLINED_REPLY",
    "LIMIT_ATTRIBUTE",
    "PAYOUT_WAIT_REPLY",
    "POLICY_APPROVER",
    "REQUEST_PAYOUT",
    "REQUEST_PAYOUT_SPEC",
    "PayoutRequested",
    "PayoutWork",
    "payout_tool",
]
