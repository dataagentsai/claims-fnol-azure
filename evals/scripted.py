"""A scenario's `model:` block, as an in-process scripted client — the regression
suite's path to the same answers the provider twin serves over the wire.

An absent `model:` block is an empty script, which raises if the model is
called at all: a scenario expecting a deterministic answer proves it from outside.
"""

from __future__ import annotations

from pathlib import Path

from agent_harness.llm import ScriptedClient
from agenttwin import ScenarioFile, load_scenario

from claims_fnol.contracts import ModelResponse, ToolCall, Usage

SCENARIOS = (
    Path(__file__).resolve().parents[2]
    / "clean-ai-engineering"
    / "gates"
    / "motor-claims-fnol"
    / "scenarios"
)


def model_for(scenario: ScenarioFile | Path | str) -> ScriptedClient:
    """The scripted model a scenario declares (its path, or its file stem)."""
    if isinstance(scenario, str):
        scenario = SCENARIOS / f"{scenario}.yaml"
    if isinstance(scenario, Path):
        scenario = load_scenario(scenario)
    return ScriptedClient(
        ModelResponse(
            text=turn.says,
            tool_calls=tuple(
                ToolCall(id=f"s{n}_{i}", name=name, arguments=dict(arguments))
                for i, call in enumerate(turn.calls, start=1)
                for name, arguments in call.items()
            ),
            usage=Usage(input_tokens=5, output_tokens=2),
        )
        for n, turn in enumerate(scenario.scripted_answers(), start=1)
    )


__all__ = ["SCENARIOS", "model_for"]
