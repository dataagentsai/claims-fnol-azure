"""Every value this agent transcribed from the AOAS or the profile, held to its source.

The Tier 2 rules, the intents, the claim states, the operations each intent is
dealt with by, the cap and the queue ttl, the scopes — each written once in code
and read here from where it is stated, so the two cannot part silently.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from agent_harness.escalation import DEFAULT_TTL_S
from kit import AOAS

from claims_fnol.binding import SCOPES
from claims_fnol.contracts import ClaimStatus, Intent, PolicyStatus
from claims_fnol.escalation.rules import DEFAULT_RULES, MAX_PER_CONVERSATION
from claims_fnol.router import VIA

SPEC = yaml.safe_load(AOAS.read_text())
ESCALATION = SPEC["policies"]["escalation"]
PROFILE = yaml.safe_load((Path(__file__).parents[1] / "harness-profile.yaml").read_text())
UNITS = {"m": 60, "h": 3600, "s": 1}
OURS = {r.id: r for r in DEFAULT_RULES}


def seconds(ttl: str) -> int:
    return int(ttl[:-1]) * UNITS[ttl[-1]]


@pytest.mark.discharges(*[f"esc:{r['id']}" for r in ESCALATION["on_condition"]])
@pytest.mark.parametrize(
    "rule", ESCALATION["on_condition"], ids=[r["id"] for r in ESCALATION["on_condition"]]
)
def test_every_tier_2_rule_is_the_aoas_rule(rule: dict[str, object]) -> None:
    ours = OURS[str(rule["id"])]
    assert ours.priority == rule["priority"]
    assert ours.ttl_s == seconds(str(rule["ttl"]))
    when = rule["when"]
    assert isinstance(when, dict)
    (condition,) = ours.when
    assert condition.field == when["field"]
    if "equals" in when:
        assert set(condition.equals or ()) == set(when["equals"])
    else:
        assert condition.at_least == when["at_least"]


@pytest.mark.discharges("P-ESC-CAP", "P-ESC-TTL")
def test_the_cap_and_the_queue_ttl_are_the_aoas() -> None:
    statements = {s["id"]: s for s in ESCALATION["statements"]}
    assert MAX_PER_CONVERSATION == statements["P-ESC-CAP"]["at_most"] == 2
    assert seconds(statements["P-ESC-TTL"]["ttl"]) == DEFAULT_TTL_S


@pytest.mark.discharges("P-DIRECT", "P-CONCERNS")
def test_the_intents_are_the_aoas_intents_and_each_is_dealt_with_by_its_via() -> None:
    assert {i.value for i in Intent} == set(SPEC["intents"])
    for intent, tools in VIA.items():
        assert set(tools) == set(SPEC["intents"][str(intent)]["via"]), intent


@pytest.mark.discharges("P-CLAIM-STATUS")
def test_the_states_are_the_aoas_states() -> None:
    assert {s.value for s in ClaimStatus} == set(SPEC["state_machines"]["claim_status"]["states"])
    assert {s.value for s in PolicyStatus} == set(
        SPEC["entities"]["policy"]["fields"]["status"]["values"]
    )


@pytest.mark.discharges("AHC-0039", "AHC-0057")
def test_the_scopes_are_the_profiles_scopes() -> None:
    assert PROFILE["bindings"]["tool_runtime"]["x_scopes"] == SCOPES
    agent_authority = {n for n, op in SPEC["operations"].items() if op.get("authority") == "agent"}
    assert "issue_payout" not in agent_authority and SCOPES["issue_payout"] == "payouts:write"
