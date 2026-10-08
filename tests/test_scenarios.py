"""Every scenario runs in process, through the agent contract, against this implementation.

Two sets, one runner. The yardstick's 29 scenarios (`clean-ai-engineering/gates/
motor-claims-fnol/scenarios`) are what every implementation is gated against;
this agent's own (`tests/scenarios`) reach the statements the yardstick does not —
the escalation desk's rules, the approval queue's, the step budget. Each case
discharges exactly what its scenario file says it discharges.

AHC-0010 by what this does not need: each scenario drives a unit of work end to
end through `Agent.handle`, with no server, no queue and no interface.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from agenttwin import (
    Clock,
    Live,
    attack_cases,
    load,
    load_scenario,
    perturbed,
    plant,
    provider_faults,
    run_file,
    timeline_for,
)
from evals.scripted import SCENARIOS as YARDSTICK
from evals.scripted import model_for
from evals.simulation import subject_for

OURS = Path(__file__).parent / "scenarios"
ALL = sorted(YARDSTICK.glob("*.yaml")) + sorted(OURS.glob("*.yaml"))


def _case(path: Path) -> object:
    scenario = load_scenario(path)
    return pytest.param(
        path,
        id=f"{'ours' if path.parent == OURS else 'yardstick'}/{path.stem}",
        marks=pytest.mark.discharges(*scenario.discharges, "AHC-0010", "AHC-0022"),
    )


RUNNABLE = [_case(p) for p in ALL if load_scenario(p).generate is None]
GENERATED = [_case(p) for p in ALL if load_scenario(p).generate is not None]


@pytest.mark.parametrize("path", RUNNABLE)
async def test_a_declared_scenario_passes_every_check_it_makes(path: Path) -> None:
    scenario = load_scenario(path)
    live = Live.start(load(path.parent / scenario.world))
    timeline = timeline_for(scenario)
    clock = Clock(step_s=scenario.step_seconds)
    wrap = perturbed(live, timeline, clock)
    async with subject_for(
        live,
        llm=model_for(scenario),
        wrap=wrap,
        clock=clock,
        provider_faults=provider_faults(scenario),
    ) as subject:
        record, outcomes = await run_file(
            path, subject=subject, live=live, timeline=timeline, clock=clock
        )
    failed = [f"{o.check} — {o.detail}" for o in outcomes if not o.passed]
    assert failed == [], f"{scenario.scenario}:\n  " + "\n  ".join(failed)
    assert record.discharges == scenario.discharges


@pytest.mark.parametrize("path", GENERATED)
async def test_every_generated_attack_case_leaves_the_world_alone(path: Path) -> None:
    """One declaration, every attack it generates, a fresh world for each. The
    model obeys the planted instruction completely; nothing it is talked into
    reaches an effect (AAC-0106, P-OWN-WORDS)."""
    scenario = load_scenario(path)
    cases = attack_cases(scenario)
    assert scenario.generate is not None and len(cases) == scenario.generate.count
    for name, payload in cases:
        live = Live.start(load(path.parent / scenario.world))
        plant(live, scenario, payload)
        timeline = timeline_for(scenario)
        async with subject_for(
            live, llm=model_for(scenario), wrap=perturbed(live, timeline)
        ) as subject:
            _, outcomes = await run_file(path, subject=subject, live=live, timeline=timeline)
        failed = [f"{o.check} — {o.detail}" for o in outcomes if not o.passed]
        assert failed == [], f"{name} ({payload[:60]}…):\n  " + "\n  ".join(failed)
