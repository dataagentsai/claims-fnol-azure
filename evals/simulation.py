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
from claims_fnol.contracts import Clock, Identity, LLMClient
from evals import durable

SESSION_META = "aoas/session"

DECISIONS = {
    "grant": Approver.grants,
    "refuse": Approver.denies,
    "never": Approver.silent,
    "grant-twice": Approver.grants_twice,
}
RESOLUTIONS = {"handled": Desk.answers, "never": Desk.never_comes}


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
) -> AsyncIterator[Subject]:
    """Wire this agent against a live world and hand back what a scenario drives.

    `wrap` is the scenario's faults: forwarded to the projection, never read."""
    metering = None
    if config is not None:

        def metering() -> Meter:  # noqa: F811
            return Meter(config.model, ceiling_usd=config.budgets.max_cost_usd)

    llm = ResilientLLM(llm)  # wrapped exactly as the deployment wraps it (F-029)
    world = project(
        live, scopes=SCOPES, wrap=wrap, authorise=as_policyholder, unknown_record="result"
    )
    async with (
        durable.server(clock) as waits,
        connect(world, requests=InMemoryRequests()) as tools,
        waits.worker(tools),
    ):
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

        async def say(
            text: str, customer_id: str, conversation: object
        ) -> tuple[str, Conversation]:
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

        yield Subject(say=say, reviewer=reviewer, colleague=colleague, opens=opens)


__all__ = ["as_policyholder", "policyholder", "subject_for"]
