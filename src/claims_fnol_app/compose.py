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

The `config` port (A6) is built first, on its own: the payout policy reads the
automatic limit through it at every decision, and the policy is one of the
hooks every other adapter is handed. The kill switch (A13) reads `agent.enabled`
through it before every turn: the agent is wrapped in the harness's `Switched`.
So `compose` builds `secrets` and `config`, then the rest; the secrets reader is
built twice, which costs nothing.

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
from agent_harness.config.settings import Settings as ConfigPort
from agent_harness.entrypoint import switch
from starlette.applications import Starlette

from claims_fnol import binding
from claims_fnol import entrypoint as ep
from claims_fnol.approvals import KEYS, PayoutWork, Policy
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


@asynccontextmanager
async def settings(planned: adapters.Plan) -> AsyncIterator[ConfigPort]:
    """The config port the overlay binds, with this agent's declared keys: the
    payout limit (A6) and the kill switch, `agent.enabled` (A13)."""
    wired = {"config": {"keys": (*KEYS, switch.ENABLED)}}
    async with adapters.compose(planned, hooks=wired, ports=("secrets", "config")) as built:
        yield built["config"]


def hooks(
    *, settings: ConfigPort, script: Iterable[ModelResponse] = (), claims_server: Any = None
) -> dict[str, dict[str, Any]]:
    """What this agent hands the adapters: whichever are chosen read their own."""
    policy = Policy(settings=settings)

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
    planned: adapters.Plan, *, script: Iterable[ModelResponse] = (), claims_server: Any = None
) -> AsyncIterator[Starlette]:
    """The app, on the planned adapters, with every connection closed on exit."""
    rest = tuple(port for port in planned.bound if port != "config")
    async with settings(planned) as limits:
        wired = hooks(settings=limits, script=script, claims_server=claims_server)
        config = wired["model"]["choice"]
        async with adapters.compose(planned, hooks=wired, ports=rest) as built:
            counted = Counted(built["model"])
            waits = built["approval"]
            built_agent = ep.build(
                llm=counted,
                tools=built["tool_runtime"].client,
                store=built["state"].checkpoints,
                approvals=waits.approvals,
                escalations=waits.escalations,
                deliveries=built["state"].requests,
                config=config,
            )
            # A13: every turn asks `agent.enabled` first; false is the paused reply.
            agent = switch.Switched(built_agent, limits, reply=binding.PAUSED)
            usage_route = bool(planned.app.get("usage_route", False))
            yield edge.build(
                agent, waits, built["identity"], counted=counted, usage_route=usage_route
            )


__all__ = ["CONFIG", "compose", "hooks", "migrate_agent_state", "overlay", "settings"]
