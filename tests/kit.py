"""What the tests share: the yardstick's world, an agent wired to it, and scripted answers.

The world is the gates' own (`clean-ai-engineering/gates/motor-claims-fnol/worlds`),
projected through the same binding hook the gates use, so a test here and a
scenario there meet the same claims system.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from agent_harness.llm import ScriptedClient
from agent_harness.requests import InMemoryRequests
from agent_harness.state import InMemoryCheckpointStore
from agent_harness.tools import connect
from agenttwin import Live, load, project
from evals.scripted import SCENARIOS
from evals.simulation import Entities, as_policyholder, entities_of, policyholder

from claims_fnol import entrypoint as ep
from claims_fnol.binding import SCOPES
from claims_fnol.contracts import (
    Identity,
    LLMClient,
    ModelResponse,
    ToolCall,
    ToolClient,
    Usage,
)

WORLD = SCENARIOS.parent / "worlds" / "motor-claims-fnol.yaml"
AOAS = Path(__file__).resolve().parents[2] / (
    "clean-ai-engineering/drafts/examples/motor-claims-fnol.aoas.yaml"
)
ROHAN = "PH-1001"
MEERA = "PH-1002"


def live() -> Live:
    return Live.start(load(WORLD))


def me(customer_id: str = ROHAN) -> Identity:
    return policyholder(customer_id)


def says(text: str = "", *calls: tuple[str, dict[str, object]], out: int = 2) -> ModelResponse:
    """One scripted model answer: words, or tool calls, or both."""
    return ModelResponse(
        text=text,
        tool_calls=tuple(
            ToolCall(id=f"c{i}", name=name, arguments=args)
            for i, (name, args) in enumerate(calls, start=1)
        ),
        usage=Usage(input_tokens=10, output_tokens=out),
    )


@asynccontextmanager
async def claims_system(world: Live | None = None) -> AsyncIterator[ToolClient]:
    """The projected claims system, as the agent's tool client sees it."""
    world = world or live()
    server = project(world, scopes=SCOPES, authorise=as_policyholder, unknown_record="result")
    async with connect(server, requests=InMemoryRequests()) as tools:
        yield Entities(tools, entities_of(world))


@asynccontextmanager
async def agent(
    answers: Iterable[ModelResponse] = (),
    *,
    world: Live | None = None,
    llm: LLMClient | None = None,
    **build: Any,
) -> AsyncIterator[tuple[ep.Agent, ScriptedClient]]:
    """The agent on the world, with a scripted model and no approval or escalation
    store unless one is passed (an empty script raises if the model is called)."""
    scripted = ScriptedClient(answers)
    async with claims_system(world) as tools:
        built = ep.build(
            llm=llm or scripted, tools=tools, store=InMemoryCheckpointStore(), **build
        )
        yield built, scripted
