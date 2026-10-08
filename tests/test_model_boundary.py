"""Every way the model can fail ends in a declared, typed outcome — never an exception at the caller.

The choke point, its retries and its failure vocabulary are the harness's
(`agent_harness.llm`, `agent_harness.resilience`); this table holds them to it
through this agent's own composition: the same `ResilientLLM` the binding wraps
the model in, the same entrypoint, the same results.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from agent_harness.contracts.failures import AgentFailure
from agent_harness.resilience import ResilientLLM
from kit import agent, me, says

from claims_fnol.contracts import (
    Completed,
    Escalated,
    Failed,
    ModelMalformed,
    ModelRefused,
    ModelRequest,
    ModelResponse,
    ModelThrottled,
    ModelUnavailable,
    Refused,
)

SRC = Path(__file__).resolve().parents[1] / "src" / "claims_fnol"


class Flaky:
    """Raises the given failure `times` times, then answers."""

    def __init__(self, failure: Exception, times: int) -> None:
        self.failure, self.times, self.calls = failure, times, 0

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.calls += 1
        if self.calls <= self.times:
            raise self.failure
        return says("Hello — I can help with your claims and policies.")


async def no_wait(_seconds: float) -> None:
    return None


# [failure, times it happens, fault kind, attempts made, what the caller gets]
FAILURES = [
    ("throttled once", ModelThrottled("slow down", retry_after=0.0), 1, "unreachable", 2, Completed),
    ("unavailable once", ModelUnavailable("down"), 1, "unreachable", 2, Completed),
    ("unavailable throughout", ModelUnavailable("down"), 99, "unreachable", 3, Failed),
    ("malformed", ModelMalformed("garbled", raw="{not json"), 99, "malformed", 1, Failed),
    ("refused by the provider", ModelRefused("key revoked"), 99, "refused", 1, Failed),
]


@pytest.mark.discharges("AHC-0001", "AHC-0005", "AHC-0021", "AHC-0024", "AHC-0110", "AHC-0017")
@pytest.mark.parametrize(
    ("why", "failure", "times", "fault", "attempts", "outcome"), FAILURES, ids=[f[0] for f in FAILURES]
)
async def test_each_provider_failure_has_its_declared_outcome(
    why: str, failure: AgentFailure, times: int, fault: str, attempts: int, outcome: type
) -> None:
    assert failure.fault.value == fault
    flaky = Flaky(failure, times)
    async with agent(llm=ResilientLLM(flaky, sleep=no_wait)) as (built, _):
        result, _ = await built.handle("hello, I have a question", identity=me())
    assert isinstance(result, outcome), result
    assert flaky.calls == attempts
    if isinstance(result, Failed):
        assert "garbled" not in result.customer_message and "{not json" not in result.customer_message


# [words, the typed result with no desk wired, whether the model is asked]
ROUTES = [
    ("Will my claim be approved?", Refused, False),
    ("I want to talk to a person", Refused, False),  # no desk: refused, never a pretend handoff
    ("Where is my claim CLM-010006?", Completed, False),
    ("hello, a general question", Completed, True),
]


@pytest.mark.discharges("AHC-0017", "AHC-0010", "AHC-0100")
@pytest.mark.parametrize(("words", "outcome", "asks_model"), ROUTES, ids=[r[0] for r in ROUTES])
async def test_every_route_returns_a_discriminated_result(
    words: str, outcome: type, asks_model: bool
) -> None:
    async with agent([says("Hello — I can help with your claims.")]) as (built, llm):
        result, conversation = await built.handle(words, identity=me())
    assert isinstance(result, outcome) and not isinstance(result, Escalated)
    assert bool(llm.calls) is asks_model
    assert conversation.turn_count == 1, "the turn is on the record it returned"


PROVIDERS = ("openai", "anthropic", "groq", "pydantic_ai")


def imported(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module.split(".")[0])
    return found


@pytest.mark.discharges("AHC-0004", "AHC-0022")
@pytest.mark.parametrize("path", sorted(SRC.rglob("*.py")), ids=lambda p: str(p.relative_to(SRC)))
def test_no_module_of_this_agent_knows_a_provider_sdk(path: Path) -> None:
    """One choke point: the agent reaches a model only through the harness's
    `LLMClient` port, so a scripted client, the provider twin's adapter and the
    deployment's Pydantic AI client stand in the same slot (the import contract
    in pyproject.toml holds the same line at build time)."""
    assert not imported(path) & set(PROVIDERS)
