"""This agent, as a scenario sees it — the far side of the agent contract.

Outside `src/` deliberately: the import contract says the agent cannot see its
simulator, and this is where the two meet. The world is the far end: the
projected claims system stands in for the PostgreSQL MCP server the deployment
will run (Tier 2), and the approval and escalation waits run on Temporal's
time-skipping test server, on the scenario's clock.

    async with subject_for(live, llm=..., clock=...) as subject:
        ...
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

from agent_harness.cost import Meter
from agent_harness.requests import InMemoryRequests
from agent_harness.resilience import ResilientLLM
from agent_harness.state import Conversation, InMemoryCheckpointStore
from agent_harness.tools import connect
from agenttwin import Approver, Desk, Live, Subject, project

from claims_fnol import entrypoint as ep
from claims_fnol.binding import POLICYHOLDER_SCOPES, SCOPES, SESSION_FIELD
from claims_fnol.config import RunConfig
from claims_fnol.contracts import (
    Clock,
    IdempotencyKey,
    Identity,
    LLMClient,
    ModelMalformed,
    ModelRequest,
    ModelResponse,
    ModelThrottled,
    ModelUnavailable,
    ToolClient,
    ToolRegistry,
    ToolResult,
)
from evals import durable

SESSION_META = "aoas/session"

DECISIONS = {
    "grant": Approver.grants,
    "refuse": Approver.denies,
    "never": Approver.silent,
    "grant-twice": Approver.grants_twice,
}
RESOLUTIONS = {"handled": Desk.answers, "never": Desk.never_comes}


@dataclass
class FaultyProvider:
    """A model client that misbehaves on the calls a scenario named — the
    in-process twin of the provider twin's faults. Copied from the reference
    agent's `evals/simulation.py` (FINDINGS F-3): mechanism, outside the library."""

    inner: LLMClient
    faults: dict[int, tuple[str, float | None, int]]
    clock: Clock | None = None
    calls: int = 0
    fired: list[int] = field(default_factory=list)
    outage: tuple[str, float | None, int] | None = None

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.calls += 1
        now = self.clock() if self.clock is not None else 0
        fault = self.faults.get(self.calls)
        if fault is not None:
            self.fired.append(self.calls)
            kind, retry_after, lasts_s = fault
            if lasts_s:
                self.outage = (kind, retry_after, now + lasts_s)
        elif self.outage is not None and now < self.outage[2]:
            kind, retry_after, _ = self.outage
        else:
            return await self.inner.complete(request)
        if kind == "provider_throttled":
            raise ModelThrottled("the provider is rate limiting", retry_after=retry_after)
        if kind == "provider_unavailable":
            raise ModelUnavailable("the provider could not be reached")
        raise ModelMalformed("the provider returned something unreadable", raw="{not json")


@dataclass
class Entities:
    """The projected claims system, with each operation's entity declared.

    The harness's MCP client reads a tool's `entity` from its `_meta`, and the
    freshness re-read picks the reader of *that kind of row* by it (AHC-0107).
    AgentTwin's projection publishes no `entity`, so on this two-entity surface
    the harness fell back to the first read on the list — re-read a claim with
    `get_policy`, got "no policy CLM-010005", and took the answer as fresh
    (FINDINGS F-11). Declared here, at the binding, from the world's own actions.
    """

    inner: ToolClient
    entities: Mapping[str, str]

    async def list_tools(self, identity: Identity) -> ToolRegistry:
        registry = await self.inner.list_tools(identity)
        declared = tuple(
            t.model_copy(update={"entity": self.entities.get(t.name, t.entity)})
            for t in registry.tools
        )
        return registry.model_copy(update={"tools": declared})

    async def call(
        self,
        name: str,
        arguments: dict[str, object],
        identity: Identity,
        idempotency_key: IdempotencyKey,
    ) -> ToolResult:
        return await self.inner.call(name, arguments, identity, idempotency_key)


def entities_of(live: Live) -> dict[str, str]:
    return {
        name: action.entity
        for system in live.world.systems.values()
        for name, action in system.actions.items()
    }


async def as_policyholder(
    _operation: str, _arguments: dict[str, Any], meta: dict[str, object]
) -> Mapping[str, object] | None:
    """The far end's session, in the AOAS's own field.

    The harness's MCP client sends `{customer_id: ...}` under `aoas/session`
    whatever the agent's domain calls its caller, and this world's ownership
    compares `policyholder_id` (AOAS `session`). Renamed here, at the binding,
    rather than in the library (FINDINGS F-5)."""
    session = meta.get(SESSION_META)
    if not isinstance(session, dict) or not session.get("customer_id"):
        return None
    return {SESSION_FIELD: session["customer_id"]}


def policyholder(customer_id: str) -> Identity:
    return Identity(customer_id=customer_id, scopes=POLICYHOLDER_SCOPES)


@asynccontextmanager
async def subject_for(
    live: Live,
    *,
    llm: LLMClient,
    clock: Clock | None = None,
    wrap: object = None,
    config: RunConfig | None = None,
    provider_faults: tuple[tuple[int, str, float | None, int], ...] = (),
) -> AsyncIterator[Subject]:
    """Wire this agent against a live world and hand back what a scenario drives.

    `wrap` is the scenario's faults: forwarded to the projection, never read."""
    metering = None
    if config is not None:

        def metering() -> Meter:  # noqa: F811
            return Meter(config.model, ceiling_usd=config.budgets.max_cost_usd)

    if provider_faults:
        faults = {call: (kind, after, lasts) for call, kind, after, lasts in provider_faults}
        llm = FaultyProvider(llm, faults, clock=clock)
    llm = ResilientLLM(llm)  # wrapped exactly as the deployment wraps it (F-029)
    world = project(
        live, scopes=SCOPES, wrap=wrap, authorise=as_policyholder, unknown_record="result"
    )
    async with (
        durable.server(clock) as waits,
        connect(world, requests=InMemoryRequests()) as projected,
    ):
        tools = Entities(projected, entities_of(live))
        async with waits.worker(tools):
            yield _subject(tools, waits, llm, clock, config, metering)


def _subject(
    tools: ToolClient,
    waits: durable.Durable,
    llm: LLMClient,
    clock: Clock | None,
    config: RunConfig | None,
    metering: Any,
) -> Subject:
    """The agent, built on the wired tools and waits, as the three callables."""
    approvals, escalations = waits.approvals, waits.escalations
    agent = ep.build(
        llm=llm,
        tools=tools,
        store=InMemoryCheckpointStore(),
        approvals=approvals,
        escalations=escalations,
        clock=clock,
        config=config,
        metering=metering,
    )

    async def say(text: str, customer_id: str, conversation: object) -> tuple[str, Conversation]:
        held = conversation if isinstance(conversation, Conversation) else None
        result, held = await agent.handle(
            text, identity=policyholder(customer_id), conversation=held
        )
        reply = getattr(result, "reply", "") or getattr(result, "customer_message", "")
        return reply, held

    def reviewer(decision: str, by: str, delay_s: int = 0) -> Approver:
        deciding = durable.decide(waits.desk)
        return DECISIONS[decision](approvals, deciding, name=by, delay_s=delay_s)

    def colleague(resolution: str, by: str, delay_s: int = 0) -> Desk:
        resolving = durable.resolving(waits.colleagues)
        return RESOLUTIONS[resolution](escalations, resolving, name=by, delay_s=delay_s)

    async def opens(customer_id: str) -> str:
        return await agent.opening(policyholder(customer_id))

    return Subject(say=say, reviewer=reviewer, colleague=colleague, opens=opens)


__all__ = [
    "Entities",
    "FaultyProvider",
    "as_policyholder",
    "entities_of",
    "policyholder",
    "subject_for",
]
