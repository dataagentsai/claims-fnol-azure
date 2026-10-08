"""The AOAS's required properties, enforced by the harness in this build and held by tables.

Q-STEPS, Q-COST, Q-OUTPUT, Q-TOOL-RESULT and Q-MODEL are numbers the AOAS
states; each is read from the AOAS here, compared with what this build
configures, and then shown to stop real work — a ceiling that is configured and
never reached is the clothing agent's F-019.
"""

from __future__ import annotations

import re

import pytest
import yaml
from agent_harness.config import Budgets
from agent_harness.cost import Meter, UnknownPrice
from kit import AOAS, agent, live, me, says

from claims_fnol.config import RunConfig, Settings, UnapprovedModel, resolve
from claims_fnol.contracts import TerminationReason

SPEC = yaml.safe_load(AOAS.read_text())
REQUIRED = {p["id"]: p["must"] for p in SPEC["required"]["properties"]}


def number(prop: str) -> float:
    found = re.search(r"(\d+(?:\.\d+)?)", REQUIRED[prop])
    assert found, prop
    return float(found.group(1))


# [property, the number this build enforces]
NUMBERS = [
    ("Q-STEPS", lambda c: c.budgets.max_steps),
    ("Q-COST", lambda c: c.budgets.max_cost_usd),
    ("Q-OUTPUT", lambda c: c.budgets.max_output_tokens),
    ("Q-TOOL-RESULT", lambda c: c.budgets.max_tool_result_chars),
]


@pytest.mark.discharges("Q-STEPS", "Q-COST", "Q-OUTPUT", "Q-TOOL-RESULT", "AHC-0003")
@pytest.mark.parametrize(("prop", "configured"), NUMBERS, ids=[n[0] for n in NUMBERS])
def test_every_required_number_is_the_aoas_number(prop: str, configured: object) -> None:
    config = resolve(Settings())
    assert configured(config) == number(prop)  # type: ignore[operator]


def config(**overrides: object) -> RunConfig:
    return resolve(Settings(**overrides))  # type: ignore[arg-type]


LOOKUPS = [says("", ("get_claim", {"id": f"CLM-01000{1 + i % 8}"})) for i in range(20)]


@pytest.mark.discharges("Q-STEPS", "AHC-0041", "esc:loop-exhausted")
async def test_the_step_budget_stops_the_turn_at_twelve() -> None:
    async with agent(LOOKUPS, config=config()) as (built, llm):
        result, _ = await built.handle("where are CLM-010001 and CLM-010002?", identity=me())
    assert len(llm.calls) == 12
    assert result.termination is TerminationReason.STEP_BUDGET_EXHAUSTED


@pytest.mark.discharges("Q-COST", "AHC-0030", "AHC-0007")
async def test_the_cost_ceiling_stops_work_in_flight() -> None:
    expensive = [says("", ("get_claim", {"id": "CLM-010001"}), out=400_000) for _ in range(5)]
    async with agent(expensive, config=config(max_cost_usd=0.5)) as (built, llm):
        result, _ = await built.handle("where are CLM-010001 and CLM-010003?", identity=me())
    # 400k output tokens at $0.75/Mtok is $0.30 a call: the second crosses $0.50.
    assert len(llm.calls) == 2
    assert result.termination is TerminationReason.COST_CEILING_REACHED


@pytest.mark.discharges("AHC-0101", "AHC-0007")
def test_an_unpriced_model_fails_where_the_run_is_configured() -> None:
    with pytest.raises(UnknownPrice):
        Meter("someone/unpriced-model", ceiling_usd=0.5)
    assert Meter(config().model, ceiling_usd=0.5).remaining > 0


@pytest.mark.discharges("Q-OUTPUT", "AHC-0014", "AHC-0025")
async def test_every_call_asks_for_at_most_the_output_bound_and_a_cut_reply_is_typed() -> None:
    cut = says("The claim is approv").model_copy(update={"stop_reason": "length"})
    async with agent([cut], config=config()) as (built, llm):
        result, _ = await built.handle("hello, a question about my claims", identity=me())
    assert all(call.max_tokens == 4096 for call in llm.calls)
    assert result.termination is TerminationReason.OUTPUT_LENGTH_REACHED
    assert "approv" not in getattr(result, "reply", "")


@pytest.mark.discharges("Q-TOOL-RESULT", "AHC-0038")
async def test_a_tool_result_enters_context_at_most_eight_thousand_characters() -> None:
    world = live()
    world.rows["claim"]["CLM-010005"]["note"] = "windscreen " * 3000  # 33,000 characters
    answers = [says("", ("get_claim", {"id": "CLM-010005"})), says("CLM-010005 is registered.")]
    async with agent(answers, world=world) as (built, llm):
        await built.handle("any news on CLM-010005 and CLM-010006?", identity=me())
    tool_messages = [m for m in llm.calls[-1].messages if m.role == "tool"]
    assert tool_messages and all(len(m.content) <= 8000 + 200 for m in tool_messages)


# [model, approved]
MODELS = [
    ("openai/gpt-oss-120b", True),
    ("openai/gpt-oss-20b", True),
    ("llama-3.3-70b-versatile", False),
    ("gpt-4o", False),
]


@pytest.mark.discharges("Q-MODEL", "AHC-0009", "AHC-0003")
@pytest.mark.parametrize(("model", "approved"), MODELS, ids=[m[0] for m in MODELS])
def test_only_an_approved_model_can_be_configured(model: str, approved: bool) -> None:
    if approved:
        assert resolve(Settings(model=model)).model == model
    else:
        with pytest.raises(UnapprovedModel):
            resolve(Settings(model=model))


@pytest.mark.discharges("Q-MODEL", "AHC-0003")
def test_the_profile_pin_is_approved_and_in_the_fingerprint() -> None:
    from pathlib import Path

    profile = yaml.safe_load((Path(__file__).parents[1] / "harness-profile.yaml").read_text())
    pinned = profile["bindings"]["model"]["x_model"]["id"]
    assert pinned in Settings().approved_models and Settings().model == pinned
    assert config().fingerprint != config(temperature=0.2).fingerprint
    assert config().fingerprint == config(mcp_base_url="http://elsewhere/mcp").fingerprint


@pytest.mark.discharges("AHC-0097")
async def test_one_step_planning_past_the_fan_out_bound_runs_none_of_it() -> None:
    many = says("", *[("get_claim", {"id": f"CLM-01000{1 + i % 8}"}) for i in range(9)])
    async with agent([many]) as (built, _):
        result, _ = await built.handle("where are CLM-010001 and CLM-010002?", identity=me())
    assert result.termination is TerminationReason.TOOL_CALL_BUDGET_EXHAUSTED
    assert Budgets().max_tool_calls_per_step == 8


@pytest.mark.discharges("AHC-0096")
async def test_a_turn_past_its_clock_ends_as_deadline_reached() -> None:
    moments = iter([0, 0, 0, 120, 120, 120, 120, 120, 120])
    answers = [says("", ("get_claim", {"id": "CLM-010001"})), says("done")]
    async with agent(answers, clock=lambda: next(moments)) as (built, _):
        result, _ = await built.handle("where are CLM-010001 and CLM-010003?", identity=me())
    assert result.termination is TerminationReason.DEADLINE_REACHED
