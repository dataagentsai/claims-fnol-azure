"""The composition root: which adapter fills each port, chosen by environment.

`CLAIMS_FNOL_ENV` selects one row:

- **local** (this Mac): Groq directly through the Pydantic AI layer; our claims
  system's MCP server over HTTP; DBOS waits and `agent_state` on the local
  PostgreSQL; the local test sign-in; one console line per model call.
- **test** (the app's own tests): the same, with a scripted model and the
  claims server in process, on throwaway databases.
- **azure** (Tier 4): APIM in front of Groq, the same claims server on
  Container Apps, DBOS and `agent_state` on Azure PostgreSQL, Entra ID sign-in,
  App Insights. A stub here that names those adapters.

The gates never come here: `evals/gate_binding.py` wires the agent to the
AgentTwin world with the scripted model, and keeps doing so.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from importlib import resources
from typing import Any, Literal

import psycopg
from agent_harness.llm.pydantic_ai import openai_compatible
from agent_harness.requests.postgres import PostgresRequests
from agent_harness.resilience import ResilientLLM
from agent_harness.state.postgres import PostgresCheckpointStore
from agent_harness.tools import connect
from psycopg_pool import AsyncConnectionPool
from starlette.applications import Starlette

from claims_fnol import entrypoint as ep
from claims_fnol.config import RunConfig, Settings, resolve
from claims_fnol.contracts import LLMClient
from claims_fnol_app import edge, waits
from claims_fnol_app.signin import LocalIssuer
from claims_fnol_app.usage import Counted

Env = Literal["local", "test", "azure"]
ENVIRONMENTS: tuple[Env, ...] = ("local", "test", "azure")

AZURE = (
    "CLAIMS_FNOL_ENV=azure is wired in Tier 4. Its adapters: the model through APIM "
    "(agent_harness.llm.pydantic_ai.openai_compatible with key_header="
    "'Ocp-Apim-Subscription-Key'), sign-in by Entra ID (agent_harness.identity.entra), "
    "telemetry to App Insights (agent_harness.telemetry.azure), the DBOS waits and "
    "agent_state on Azure Database for PostgreSQL over TLS, and the claims system's "
    "MCP server on Container Apps behind a verified token."
)


@dataclass(frozen=True)
class Wiring:
    """Everything the root reads from the environment, read once."""

    env: Env
    agent_database_url: str
    """The agent's database: the DBOS waits' tables and `agent_state`."""
    claims: Any
    """The claims system: its MCP URL, or (test) an MCP server in this process."""
    api_key: str = ""
    llm: LLMClient | None = None
    """A model the caller supplies (test); otherwise built from `Settings`."""


def env_name() -> Env:
    named = os.environ.get("CLAIMS_FNOL_ENV", "local")
    if named not in ENVIRONMENTS:
        raise SystemExit(f"CLAIMS_FNOL_ENV={named!r}: expected one of {ENVIRONMENTS}")
    return named  # type: ignore[return-value]


def model(wiring: Wiring) -> tuple[Counted, RunConfig | None]:
    """The model, counted and behind the harness's retries and breaker; and the
    resolved configuration whose budgets and cost ceiling bound every turn."""
    if wiring.llm is not None:
        return Counted(wiring.llm), None
    if not wiring.api_key:
        raise SystemExit("no model key: put GROQ_API_KEY in .env (see .env.example)")
    config = resolve(Settings())
    client = openai_compatible(
        api_key=wiring.api_key,
        base_url=config.provider_base_url,
        model=config.model,
        provider=config.provider,
        temperature=config.temperature,
    )
    return Counted(ResilientLLM(client)), config


async def migrate_agent_state(url: str) -> None:
    """Create `agent_state` if it is not there (idempotent DDL)."""
    sql = (resources.files("claims_fnol_app") / "migrations" / "001_agent_state.sql").read_text()
    async with await psycopg.AsyncConnection.connect(url, autocommit=True) as conn:
        await conn.execute(sql.encode())


@asynccontextmanager
async def _pool(url: str) -> AsyncIterator[AsyncConnectionPool[Any]]:
    pool: AsyncConnectionPool[Any] = AsyncConnectionPool(url, min_size=1, max_size=4, open=False)
    await pool.open(wait=True, timeout=10)
    try:
        yield pool
    finally:
        await pool.close()


@asynccontextmanager
async def compose(wiring: Wiring) -> AsyncIterator[Starlette]:
    """The app, with every connection it holds opened here and closed on exit."""
    if wiring.env == "azure":
        raise NotImplementedError(AZURE)
    await migrate_agent_state(wiring.agent_database_url)
    llm, config = model(wiring)
    async with AsyncExitStack() as stack:
        pool = await stack.enter_async_context(_pool(wiring.agent_database_url))
        ledger = PostgresRequests(pool)
        tools = await stack.enter_async_context(connect(wiring.claims, requests=ledger))
        # The payout worker's own connection, as a deployment's worker has its own.
        worker = await stack.enter_async_context(connect(wiring.claims, requests=ledger))
        held = await stack.enter_async_context(waits.running(wiring.agent_database_url, worker))
        agent = ep.build(
            llm=llm,
            tools=tools,
            store=PostgresCheckpointStore(pool),
            approvals=held.approvals,
            escalations=held.escalations,
            deliveries=ledger,
            config=config,
        )
        yield edge.build(agent, held, LocalIssuer(), counted=llm, env=wiring.env)


def wiring_from_env() -> Wiring:
    """The local row, from the environment and `.env`."""
    from claims_fnol_app.settings import setting

    return Wiring(
        env=env_name(),
        agent_database_url=setting("CLAIMS_DBOS_DATABASE_URL"),
        claims=setting("CLAIMS_MCP_URL", "http://127.0.0.1:9050/mcp"),
        api_key=setting("GROQ_API_KEY", "") or setting("CLAIMS_PROVIDER_API_KEY", ""),
    )


__all__ = ["AZURE", "ENVIRONMENTS", "Env", "Wiring", "compose", "env_name", "model"]
