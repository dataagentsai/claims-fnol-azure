"""Every intent and refusal example the AOAS gives is routed the way the AOAS says it is answered.

The cases are read from the AOAS, never copied here: an example added there is
a case here (the clothing agent's lesson, T-094). Direct examples naming one
reference go to that intent's handler; naming none, to the loop (P-DIRECT).
"""

from __future__ import annotations

import pytest
import yaml
from kit import AOAS

from claims_fnol.contracts import Agentic, Direct, Escalate, Refuse, Route
from claims_fnol.router import DIRECT_HANDLERS, route

SPEC = yaml.safe_load(AOAS.read_text())

HANDOFF_RULE = {
    "human": "asked-for-human",
    "injury": "injury-reported",
    "roadside_assistance": "outside-scope",
}
"""The AOAS's handoff and deferred intents, and the on_request rule each raises."""

# [AOAS intent, how it is answered, example]
EXAMPLES = [
    (name, intent["answered_by"], example)
    for name, intent in SPEC["intents"].items()
    for example in intent["examples"]
]
# [refusal id, example]
REFUSALS = [(r["id"], e) for r in SPEC["purpose"]["refuses"] for e in r["examples"]]


def recognised(decision: Route) -> set[str]:
    if isinstance(decision, Direct):
        return {decision.intent.value}
    if isinstance(decision, Agentic):
        return {i.value for i in decision.candidate_intents}
    return set()


@pytest.mark.discharges(
    "P-DIRECT", "AHC-0100", "esc:asked-for-human", "esc:injury-reported", "esc:outside-scope"
)
@pytest.mark.parametrize(
    ("intent", "answered_by", "example"), EXAMPLES, ids=[e[2] for e in EXAMPLES]
)
def test_every_aoas_example_is_read_as_its_intent(
    intent: str, answered_by: str, example: str
) -> None:
    decision = route(example)
    if answered_by in ("handoff", "deferred"):
        assert isinstance(decision, Escalate), decision
        assert decision.rule_id == HANDOFF_RULE[intent]
    elif answered_by == "refusal":
        assert isinstance(decision, Refuse), decision
        assert decision.rule_id == SPEC["intents"][intent]["refusal"]
    elif intent == "other":
        assert recognised(decision) == set(), decision
    else:
        assert recognised(decision) == {intent}, decision


@pytest.mark.discharges(
    "R-COVERAGE",
    "R-LIABILITY",
    "R-OTHER-HOLDER",
    "R-POLICY-CHANGE",
    "R-FRAUD",
    "R-LEGAL",
    "P-DIRECT",
    "AHC-0100",
)
@pytest.mark.parametrize(("rule", "example"), REFUSALS, ids=[r[1] for r in REFUSALS])
def test_every_refusal_example_is_refused_by_its_own_rule(rule: str, example: str) -> None:
    decision = route(example)
    assert isinstance(decision, Refuse) and decision.rule_id == rule, decision


DIRECT = [(n, e) for n, a, e in EXAMPLES if a == "direct"]
SAMPLE_REF = {
    "claim_status": "CLM-010006",
    "payout_status": "CLM-010001",
    "policy_status": "POL-010001",
}


@pytest.mark.discharges("P-DIRECT", "P-DIRECT-READS", "AHC-0100")
@pytest.mark.parametrize(("intent", "example"), DIRECT, ids=[d[1] for d in DIRECT])
def test_a_direct_example_naming_one_reference_is_answered_by_its_handler(
    intent: str, example: str
) -> None:
    ref = SAMPLE_REF[intent]
    named = example if ref[:4] in example else f"{example.rstrip('?.!')} — {ref}?"
    decision = route(named)
    assert isinstance(decision, Direct), decision
    assert decision.handler == DIRECT_HANDLERS[decision.intent]
    assert decision.handler == intent


# [words, why it must go to the loop]
TO_THE_LOOP = [
    ("Where are CLM-010001 and CLM-010003?", "two claims named"),
    ("What's happening with my claim?", "no claim named"),
    ("Is my policy POL-010001 active, and where is CLM-010006?", "two intents"),
]


@pytest.mark.discharges("P-DIRECT")
@pytest.mark.parametrize(("words", "why"), TO_THE_LOOP, ids=[t[1] for t in TO_THE_LOOP])
def test_several_or_no_references_go_to_the_loop(words: str, why: str) -> None:
    assert isinstance(route(words), Agentic), why
