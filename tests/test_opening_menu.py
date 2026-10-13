"""The opening says what the agent can and cannot do, held to the AOAS.

`purpose.does` is read from the AOAS, never copied here: a thing added there
fails this table until the opening names it too.
"""

from __future__ import annotations

import pytest
import yaml
from kit import AOAS

from claims_fnol.entrypoint.opening import CAN_DO, CANNOT_DO, _words

SPEC = yaml.safe_load(AOAS.read_text())
DOES = SPEC["purpose"]["does"]
REFUSES = {r["id"] for r in SPEC["purpose"]["refuses"]}


@pytest.mark.parametrize("does", DOES)
def test_each_thing_the_aoas_says_it_does_is_offered(does: str) -> None:
    assert does in CAN_DO, f"the opening does not offer: {does}"
    assert f"- {CAN_DO[does]}" in _words([], [], [])


def test_the_opening_offers_nothing_the_aoas_does_not_list() -> None:
    assert set(CAN_DO) <= set(DOES)


@pytest.mark.parametrize(
    ("refusal", "said"),
    [("R-COVERAGE", "covered"), ("R-LIABILITY", "fault")],
)
def test_the_two_likeliest_refusals_are_said_up_front(refusal: str, said: str) -> None:
    assert refusal in REFUSES
    assert any(said in line for line in CANNOT_DO)
    assert said in _words([], [], [])


SHOWN = [
    # (claims, policies, a line the opening has, a line it has not)
    ([], [], "You have no open claims.", "I cannot see your claims"),
    (None, [], "I cannot see your claims", "You have no open claims."),
    ([{"id": "CLM-1", "status": "registered"}], [], "- CLM-1: registered", "no open claims"),
]


@pytest.mark.parametrize(("claims", "policies", "has", "lacks"), SHOWN)
def test_the_menu_comes_after_the_claims_whatever_they_are(
    claims: list | None, policies: list, has: str, lacks: str
) -> None:
    words = _words(claims, policies, [])
    assert has in words and lacks not in words
    assert words.index("I can:") > words.index("Hello.")
    assert words.rstrip().endswith("What can I help with?")
