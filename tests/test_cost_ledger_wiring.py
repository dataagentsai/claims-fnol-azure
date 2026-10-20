"""Each turn's spend reaches the composed cost ledger (T-048).

The profile has bound `cost_ledger: per-task-meter` since the Open Stack, and
nothing built it: the meter kept its own tally and the port stood empty. Now
every overlay binds it, the composition builds it, and the composition root
hands it to each turn's meter, which records every model call under the turn's
unit with the attempt that answered.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from agent_harness import adapters
from agent_harness.contracts import ModelResponse
from agent_harness.cost.port import CostLedger, Observation, Spent
from kit import agent, me, says

from claims_fnol.config import Settings, resolve
from claims_fnol_app.compose import overlay

ENVIRONMENTS = ["local", "test", "azure"]


@pytest.mark.parametrize("environment", ENVIRONMENTS)
def test_every_overlay_binds_the_cost_ledger(environment: str) -> None:
    assert adapters.plan(overlay(environment)).adapter("cost_ledger") == "per-task-meter"


# (why, the model's answers, model calls the turn's unit records)
TURNS = [
    ("a reply in one call", [says("Which policy is this about?")], 1),
    (
        "a tool call, then the reply",
        [
            says("", ("register_claim", {"id": "POL-010001", "incident_type": "collision"})),
            says("I've registered the collision on your policy."),
        ],
        2,
    ),
]


class Watched:
    """The composed ledger, noting which units were recorded into."""

    def __init__(self, inner: CostLedger) -> None:
        self.inner, self.units = inner, set[str]()

    def record(self, observation: Observation) -> Decimal:
        self.units.add(observation.unit)
        return self.inner.record(observation)

    def spent(self, unit: str) -> Spent:
        return self.inner.spent(unit)


@pytest.mark.parametrize(("why", "answers", "calls"), TURNS, ids=[t[0] for t in TURNS])
async def test_a_turns_model_calls_are_recorded_in_the_composed_ledger(
    why: str, answers: list[ModelResponse], calls: int
) -> None:
    planned = adapters.plan(overlay("test"))
    async with adapters.compose(planned, hooks={}, ports=("cost_ledger",)) as built:
        ledger = Watched(built["cost_ledger"])
        async with agent(answers, config=resolve(Settings()), ledger=ledger) as (turns, _):
            await turns.handle("A bus hit my car this morning.", identity=me())
    assert len(ledger.units) == 1, f"{why}: one turn is one unit of work, saw {ledger.units}"
    spent = ledger.spent(next(iter(ledger.units)))
    assert (spent.calls, spent.retries) == (calls, 0), why
    assert spent.usd > 0, f"{why}: a priced model's calls cost something"
