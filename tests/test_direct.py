"""Claim, payout and policy status, answered from the claims system with no model call.

P-CLAIM-STATUS: from the claim's status, never a date or a prediction —
`documents_pending` names how many are missing, `approved` names the amount.
P-PAYOUT-OWED: a payout-status question states the status and issues nothing.
Every case runs with an empty script, so a model call would fail the test.
"""

from __future__ import annotations

import re

import pytest
from kit import agent, live, me

from claims_fnol.contracts import ClaimStatus, Completed
from claims_fnol.entrypoint.direct import CLAIM_REPLIES

DATES = re.compile(
    r"\b(?:today|tomorrow|this week|next week|working days|business days|within|by \w+day)\b", re.I
)
PROMISES = re.compile(r"\bwill be (?:approved|paid|covered)\b|\byou(?:'ll| will) (?:get|receive)\b", re.I)

# [question, claim, status it is put in, words the reply must carry]
CLAIMS = [
    ("Where is my claim CLM-010005?", "CLM-010005", "registered", ["registered", "assessor"]),
    ("Where is my claim CLM-010006?", "CLM-010006", "documents_pending", ["2", "documents"]),
    ("Where is my claim CLM-010007?", "CLM-010007", "under_assessment", ["under assessment"]),
    ("Where is my claim CLM-010001?", "CLM-010001", "approved", ["approved", "8,750", "not been paid"]),
    ("Where is my claim CLM-010008?", "CLM-010008", "rejected", ["rejected"]),
    ("Where is my claim CLM-010002?", "CLM-010002", "paid", ["paid", "6,800"]),
    ("Where is my claim CLM-010005?", "CLM-010005", "withdrawn", ["withdrawn"]),
]


@pytest.mark.discharges("P-CLAIM-STATUS", "P-DIRECT", "P-DIRECT-READS", "P-NO-PROMISE", "op:get_claim", "AHC-0100")
@pytest.mark.parametrize(("words", "claim", "status", "carries"), CLAIMS, ids=[c[2] for c in CLAIMS])
async def test_claim_status_is_told_from_the_claim_and_nothing_else(
    words: str, claim: str, status: str, carries: list[str]
) -> None:
    world = live()
    world.rows["claim"][claim]["status"] = status
    async with agent(world=world) as (built, llm):
        result, conversation = await built.handle(words, identity=me())
    assert isinstance(result, Completed) and not llm.calls
    for word in carries:
        assert word in result.reply, (word, result.reply)
    assert not DATES.search(result.reply) and not PROMISES.search(result.reply)
    assert world.effects == []
    assert f"get_claim:{claim}" in conversation.facts.read


@pytest.mark.discharges("P-CLAIM-STATUS")
def test_every_claim_state_has_its_own_words() -> None:
    assert set(CLAIM_REPLIES) == {s.value for s in ClaimStatus}


# [question, what the reply carries, what it never says]
PAYOUTS = [
    ("has the money for CLM-010001 come through?", ["approved", "8,750", "not been made"], ["is on its way"]),
    ("has the money for CLM-010002 come through?", ["paid", "6,800"], ["not been"]),
    ("has the money for CLM-010007 come through?", ["under assessment", "no payment"], ["approved for"]),
]


@pytest.mark.discharges("P-PAYOUT-OWED", "P-CLAIM-STATUS", "P-DIRECT", "P-DIRECT-READS")
@pytest.mark.parametrize(("words", "carries", "never"), PAYOUTS, ids=[p[0].split()[4] for p in PAYOUTS])
async def test_a_payout_question_states_the_status_and_pays_nothing(
    words: str, carries: list[str], never: list[str]
) -> None:
    world = live()
    async with agent(world=world) as (built, llm):
        result, _ = await built.handle(words, identity=me())
    assert isinstance(result, Completed) and not llm.calls
    assert all(w in result.reply for w in carries), result.reply
    assert not any(w in result.reply for w in never), result.reply
    assert world.effects == []


# [question, carries]
POLICIES = [
    ("Is my policy POL-010001 active?", ["POL-010001", "active", "comprehensive", "KA-01-AB-1234"]),
    ("Is my policy POL-010002 active?", ["lapsed", "third-party"]),
    ("Is my policy POL-019001 active?", ["could not find"]),  # the stranger's: as if absent
]


@pytest.mark.discharges("op:get_policy", "P-OWNERSHIP", "P-DIRECT", "R-OTHER-HOLDER")
@pytest.mark.parametrize(("words", "carries"), POLICIES, ids=[p[0].split()[3] for p in POLICIES])
async def test_policy_status_is_told_from_the_policy(words: str, carries: list[str]) -> None:
    async with agent() as (built, llm):
        result, _ = await built.handle(words, identity=me())
    assert isinstance(result, Completed) and not llm.calls
    assert all(w in result.reply for w in carries), result.reply
    assert "DL-3C" not in result.reply and "Meera" not in result.reply
