"""The composition root: this agent's parts, on adapters chosen by configuration.

Which adapter fills each port is not decided here. It is read from an overlay,
`config/<environment>.yaml`, checked against the harness profile it names
(`harness-profile.yaml`, extending the Azure stack), and built through the
library's registry (`agent_harness.adapters`):

- **local** (this Mac): Groq directly, the claims MCP server over HTTP, DBOS
  and `agent_state` on the local PostgreSQL, the local test sign-in.
- **test** (the app's own tests): a scripted model, the claims server in
  process, throwaway databases.
- **azure** (Tier 4): exactly the profile's adapters — APIM, Azure PostgreSQL,
  Entra ID, Application Insights, Key Vault. It resolves now and runs once
  Tier 3 has made its resources.

`CLAIMS_FNOL_ENV` names the overlay file and nothing else: no line here or in
`edge.py` asks which environment it is, or which vendor an adapter is. What is
this agent's is handed to the adapters as hooks: its resolved model choice, its
payout steps and approval terms, its table's DDL.

The gates never come here: `evals/gate_binding.py` wires the agent to the
AgentTwin world with the scripted model, and keeps doing so.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager
from importlib import resources
from pathlib import Path
from typing import Any

import psycopg
from agent_harness import adapters
from starlette.applications import Starlette

from claims_fnol import entrypoint as ep
from claims_fnol.approvals import PayoutWork, Policy
from claims_fnol.config import Settings, resolve
from claims_fnol.contracts import ModelResponse, ToolClient
from claims_fnol_app import edge
from claims_fnol_app.settings import ROOT, setting
from claims_fnol_app.usage import Counted
from claims_fnol_app.waits import acting_for

CONFIG = ROOT / "config"
"""One overlay per environment: `local.yaml`, `test.yaml`, `azure.yaml`."""


def overlay(name: str | None = None) -> Path:
    """The overlay `CLAIMS_FNOL_ENV` names (default `local`), or a refusal listing them."""
    chosen = name or setting("CLAIMS_FNOL_ENV", "local")
    path = CONFIG / f"{chosen}.yaml"
    if not path.is_file():
        known = ", ".join(sorted(p.stem for p in CONFIG.glob("*.yaml")))
        raise SystemExit(f"CLAIMS_FNOL_ENV={chosen!r}: no {path.name}; overlays: {known}")
    return path


async def migrate_agent_state(url: str) -> None:
    """Create `agent_state` and our records if they are not there: every file in
    `migrations/`, in order, each idempotent DDL (FINDINGS F-24; A3)."""
    folder = resources.files("claims_fnol_app") / "migrations"
    files = sorted((p for p in folder.iterdir() if p.name.endswith(".sql")), key=lambda p: p.name)
    async with await psycopg.AsyncConnection.connect(url, autocommit=True) as conn:
        for file in files:
            await conn.execute(file.read_text().encode())


def hooks(
    *, script: Iterable[ModelResponse] = (), claims_server: Any = None
) -> dict[str, dict[str, Any]]:
    """What this agent hands the adapters: whichever are chosen read their own."""
    policy = Policy()

    def payout_work(tools: ToolClient) -> PayoutWork:
        return PayoutWork(tools, acting_for=acting_for, policy=policy)

    return {
        "model": {"choice": resolve(Settings()), "script": list(script)},
        "state": {"migrate": migrate_agent_state},
        "tool_runtime": {"server": claims_server} if claims_server is not None else {},
        "approval": {"work": payout_work, "terms": policy},
    }


@asynccontextmanager
async def compose(
    planned: adapters.Plan, *, given: dict[str, dict[str, Any]] | None = None
) -> AsyncIterator[Starlette]:
    """The app, on the planned adapters, with every connection closed on exit."""
    wired = given if given is not None else hooks()
    config = wired["model"]["choice"]
    async with adapters.compose(planned, hooks=wired) as built:
        counted = Counted(built["model"])
        waits = built["approval"]
        agent = ep.build(
            llm=counted,
            tools=built["tool_runtime"].client,
            store=built["state"].checkpoints,
            approvals=waits.approvals,
            escalations=waits.escalations,
            deliveries=built["state"].requests,
            config=config,
        )
        usage_route = bool(planned.app.get("usage_route", False))
        yield edge.build(agent, waits, built["identity"], counted=counted, usage_route=usage_route)


__all__ = ["CONFIG", "compose", "hooks", "migrate_agent_state", "overlay"]
