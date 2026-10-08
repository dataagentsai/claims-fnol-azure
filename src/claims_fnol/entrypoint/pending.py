"""Work a person must decide before the agent may finish it — payout approvals.

**P-PAYOUT** and **AHC-0057**, from the turn's side — *a payout above the
limit needs a claims handler*, and a run may not grant itself that authority.
`approvals/workflow.py` owns getting a decision made; this owns what a *turn*
does about one — offering the request, and picking up an answer that arrived while nobody was
looking.

One responsibility, behind one protocol: what the turn needs from the approval
workflow. Two questions, and the agent never branches on whether a store exists:

    offer   which harness-answered tools this turn may use to *request* approval
    resume  what became of a decision made since the last turn

The approval **returns; it never blocks**. A request checkpoints and hands back
a typed `NeedsApproval`; the next turn resumes from here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from agent_harness import telemetry as tel
from agent_harness.state import Conversation

from claims_fnol import approvals as ap
from claims_fnol.contracts import (
    Approvals,
    ApprovalState,
    Completed,
    Failed,
    IdempotencyKey,
    Identity,
    LocalTool,
    RunId,
    ToolClient,
    TurnResult,
)

PAYOUT_FAILED = "The payout could not be completed."
STILL_WAITING = (
    "That payout is still with a claims handler to authorise. Nothing has been paid yet."
)
CARRYING_OUT = "A claims handler has authorised that payout, and it is being made now."
ASKED_AGAIN = (
    "The claim changed while a claims handler was reviewing this, so nothing has been paid "
    "yet. I have asked again against the claim as it is now, and it is waiting for a "
    "claims handler to authorise."
)
STALE_UNASKED = (
    "The claim changed while a claims handler was reviewing this, so their authorisation no "
    "longer matches it. Nothing has been paid — please ask again and I will raise it "
    "against the claim as it is now."
)
"""A stale grant recorded before the workflow asked again by itself."""
STALE_CLOSED = (
    "The claim changed while a claims handler was reviewing this, and its payout can no "
    "longer be requested as it is now."
)


class PendingWork(Protocol):
    def offer(
        self,
        identity: Identity,
        run_id: RunId,
        tools: ToolClient,
        conversation_id: str = "",
    ) -> dict[str, LocalTool]: ...

    async def resume(
        self, conversation: Conversation, identity: Identity, run_id: RunId, tools: ToolClient
    ) -> tuple[TurnResult, Conversation] | None: ...


@dataclass(frozen=True)
class NoApprovals:
    """No store wired: the agent cannot request a payout at all — it does not fall
    back to issuing one — and there is never anything to resume."""

    def offer(
        self,
        identity: Identity,
        run_id: RunId,
        tools: ToolClient,
        conversation_id: str = "",
    ) -> dict[str, LocalTool]:
        return {}

    async def resume(
        self, conversation: Conversation, identity: Identity, run_id: RunId, tools: ToolClient
    ) -> tuple[TurnResult, Conversation] | None:
        return None


@dataclass(frozen=True)
class ApprovalFlow:
    approvals: Approvals

    def offer(
        self,
        identity: Identity,
        run_id: RunId,
        tools: ToolClient,
        conversation_id: str = "",
    ) -> dict[str, LocalTool]:
        tool = ap.payout_tool(
            self.approvals,
            identity=identity,
            idempotency_key=IdempotencyKey(run_id=run_id, step=0, iteration=0),
            conversation_id=conversation_id,
        )
        return {tool.spec.name: tool}

    async def resume(
        self, conversation: Conversation, identity: Identity, run_id: RunId, tools: ToolClient
    ) -> tuple[TurnResult, Conversation] | None:
        """Say what became of an approval since the last turn.

        Only says: the workflow carried out a granted payout the moment it was
        granted (T-028), so there is nothing here to execute and no clock to
        judge it by. `None` when there is nothing to resume. A still-pending
        approval short-circuits: continuing would let the customer's next
        message start work the outstanding decision may make pointless.
        """
        assert conversation.pending_approval_id is not None
        approval = await self.approvals.get(conversation.pending_approval_id)
        if approval is None:
            return None

        with tel.span("agent.approval.resume", **{"agent.approval.id": approval.id}):
            state = approval.state
            if state in (ApprovalState.ASSESSING, ApprovalState.WAITING):
                return Completed(reply=STILL_WAITING), conversation
            if state is ApprovalState.CARRYING_OUT:
                return Completed(reply=CARRYING_OUT), conversation

            cleared = conversation.model_copy(update={"pending_approval_id": None})
            if state is ApprovalState.REFUSED:
                refused = Completed(
                    reply="A claims handler reviewed this payout and could not authorise it."
                )
                return refused, cleared.recording(Completed(reply=""))
            if state is ApprovalState.EXPIRED:
                expired = Failed(
                    customer_message=(
                        "That authorisation is no longer valid — "
                        "please ask again and I will raise it afresh."
                    ),
                    detail=f"approval {approval.id} expired before anyone decided",
                )
                return expired, cleared
            if state is ApprovalState.STALE:
                fresh = approval.superseded_by
                return await self._asked_again(fresh, cleared, identity, run_id, tools)
            if state is ApprovalState.FAILED:
                return Failed(customer_message=PAYOUT_FAILED, detail=approval.result or ""), cleared
            done = Completed(
                reply=(
                    "A claims handler authorised that payout, and it has been made to the "
                    "account on your policy."
                )
            )
            return done, cleared

    async def _asked_again(
        self,
        fresh_id: str | None,
        cleared: Conversation,
        identity: Identity,
        run_id: RunId,
        tools: ToolClient,
    ) -> tuple[TurnResult, Conversation] | None:
        """A grant that went stale, told as its own outcome (P-APPROVAL-STALE).

        Nothing was attempted, so there is nothing half-done to explain and no
        failure to report — the claim moved while a person thought about it,
        and the workflow has already asked again. The conversation now waits
        on the fresh approval; one that has itself been settled by the time the
        customer speaks is reported as whatever it became.
        """
        fresh = await self.approvals.get(fresh_id) if fresh_id is not None else None
        if fresh is None:
            return Completed(reply=STALE_UNASKED), cleared
        following = cleared.model_copy(update={"pending_approval_id": fresh.id})
        if fresh.state in (ApprovalState.ASSESSING, ApprovalState.WAITING):
            return Completed(reply=ASKED_AGAIN), following
        if fresh.state is ApprovalState.FAILED:
            return Completed(reply=STALE_CLOSED), cleared
        return await self.resume(following, identity, run_id, tools)


__all__ = ["ApprovalFlow", "NoApprovals", "PendingWork"]
