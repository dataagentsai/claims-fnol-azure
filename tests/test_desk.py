"""The two queues a person reads — escalations and payout approvals — and the rules they hold.

The waits are the harness's Temporal workflows, on the test server; the
statements are the AOAS's: the queue holds the open ones oldest first and a
closed one leaves it (P-ESC-QUEUE, P-APPROVAL-QUEUE), closing records an outcome
from the declared set once (P-ESC-OUTCOME), a decision is final
(P-APPROVAL-FINAL), and nobody approves their own payout (P-APPROVER).
"""

from __future__ import annotations

import pytest
import yaml
from evals.durable import approvals_for, escalations_for
from kit import AOAS, ROHAN, claims_system, me

from claims_fnol.contracts import ApprovalState, EscalationOutcome, IdempotencyKey, RunId

SPEC = yaml.safe_load(AOAS.read_text())
STATEMENTS = {s["id"]: s for s in SPEC["policies"]["escalation"]["statements"]}


@pytest.mark.discharges("P-ESC-OUTCOME")
def test_the_outcomes_are_the_aoas_declared_set() -> None:
    assert {o.value for o in EscalationOutcome} == set(STATEMENTS["P-ESC-OUTCOME"]["values"])


async def raise_one(waits: object, conversation: str) -> object:
    return await waits.escalations.raise_for(  # type: ignore[attr-defined]
        conversation_id=conversation,
        run_id=f"run-{conversation}",
        customer_id=ROHAN,
        reason="asked for a person",
        rule_id="asked-for-human",
        rules_version="v1",
    )


@pytest.mark.discharges("P-ESC-QUEUE", "P-ESC-OUTCOME", "ext:escalation_desk", "op:escalate")
@pytest.mark.parametrize("outcome", [o.value for o in EscalationOutcome])
async def test_the_escalation_queue_is_oldest_first_and_closing_is_once(outcome: str) -> None:
    async with escalations_for() as waits:
        first = await raise_one(waits, "c-1")
        second = await raise_one(waits, "c-2")
        queued = [e.id for e in await waits.escalations.pending()]
        assert queued == [first.id, second.id]  # type: ignore[attr-defined]
        closed = await waits.colleagues.resolve(first.id, outcome=outcome, by="claims-desk-1")  # type: ignore[attr-defined]
        assert closed.outcome == outcome and closed.rule_id == "asked-for-human"
        assert [e.id for e in await waits.escalations.pending()] == [second.id]  # type: ignore[attr-defined]
        with pytest.raises(Exception, match="."):
            await waits.colleagues.resolve(first.id, outcome="resolved", by="claims-desk-2")  # type: ignore[attr-defined]


# [who decides, granted, refused because]
DECIDERS = [
    ("claims-handler-3", None, ""),
    (ROHAN, None, "customer it belongs to"),  # the policyholder whose claim it is
    ("claims-handler-3", ROHAN, "customer it belongs to"),  # a login linked to them
]


@pytest.mark.discharges("P-APPROVER", "P-APPROVAL-FINAL", "P-APPROVAL-QUEUE", "ext:approval_queue", "AHC-0057")
@pytest.mark.parametrize(("by", "by_customer", "refused"), DECIDERS, ids=["handler", "policyholder", "linked login"])
async def test_a_payout_is_decided_once_and_never_by_its_policyholder(
    by: str, by_customer: str | None, refused: str
) -> None:
    async with claims_system() as tools, approvals_for(tools) as waits:
        asked = await waits.approvals.request(
            action="issue_payout",
            args={"claim_id": "CLM-010004"},
            identity=me(),
            idempotency_key=IdempotencyKey(run_id=RunId("r-desk"), step=0, iteration=0),
        )
        assert asked.state is ApprovalState.WAITING
        assert [a.id for a in await waits.approvals.pending()] == [asked.id]
        if refused:
            with pytest.raises(Exception, match=refused):
                await waits.desk.decide(asked.id, granted=True, by=by, by_customer=by_customer)
            return
        decided = await waits.desk.decide(asked.id, granted=True, by=by)
        assert decided.granted
        assert await waits.approvals.pending() == ()
        with pytest.raises(Exception, match="already decided"):
            await waits.desk.decide(asked.id, granted=True, by=by)
