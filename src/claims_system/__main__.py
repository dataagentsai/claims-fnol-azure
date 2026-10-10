"""`python -m claims_system migrate | seed [--fresh | --if-empty] | serve [--port N]`.

Reads `CLAIMS_DATABASE_URL` and, to check payouts against the agent's own
approval records (A3), `CLAIMS_RECORDS_DATABASE_URL` from the environment or
`.env`. Both name this system's own login (A4, infra/sql/roles.sql), which
in the agent's database may only read `agent_state.approvals`; never the
agent's URL. The records connection is also opened read-only, a second guard.

How it checks its caller is its own overlay's (A1): `config/claims-system/
<CLAIMS_SYSTEM_ENV>.yaml`, default `local`, composed through the harness's
registry like the agent's — the `authorise` port, and the `config` port its
automatic payout limit is read through (A6; `server.AUTOMATIC_LIMIT`).
"""

from __future__ import annotations

import argparse
import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import yaml
from agent_harness import adapters
from agent_harness.config.settings import Key, Settings
from agent_harness.contracts.records import ApprovalRecordReader
from agent_harness.identity.far_end import Authorise
from psycopg.conninfo import make_conninfo

from claims_system import store as st

ROOT = Path(__file__).resolve().parents[2]
WORLD = ROOT.parent / "clean-ai-engineering/gates/motor-claims-fnol/worlds/motor-claims-fnol.yaml"
CONFIG = ROOT / "config" / "claims-system"
"""This system's overlays: `local.yaml`, `test.yaml`, `azure.yaml`."""


def env(name: str, default: str | None = None) -> str:
    """A setting from the environment, else from this repository's `.env`."""
    if name in os.environ:
        return os.environ[name]
    dotenv = ROOT / ".env"
    if dotenv.exists():
        for line in dotenv.read_text().splitlines():
            key, sep, value = line.partition("=")
            if sep and key.strip() == name:
                return value.strip()
    if default is None:
        raise SystemExit(f"{name} is not set (environment or .env)")
    return default


def overlay(name: str | None = None) -> Path:
    """The overlay `CLAIMS_SYSTEM_ENV` names (default `local`), or a refusal listing them."""
    chosen = name or env("CLAIMS_SYSTEM_ENV", "local")
    path = CONFIG / f"{chosen}.yaml"
    if not path.is_file():
        known = ", ".join(sorted(p.stem for p in CONFIG.glob("*.yaml")))
        raise SystemExit(f"CLAIMS_SYSTEM_ENV={chosen!r}: no {path.name}; overlays: {known}")
    return path


@asynccontextmanager
async def authorisation(
    planned: adapters.Plan, *, hooks: dict[str, Any] | None = None
) -> AsyncIterator[Authorise]:
    """The `authorise` port the overlay binds, built through the registry;
    `hooks` are the adapter's (`keys`, in a test)."""
    given = {"authorise": hooks} if hooks else None
    async with adapters.compose(planned, hooks=given, ports=("secrets", "authorise")) as built:
        yield built["authorise"]


@asynccontextmanager
async def settings(planned: adapters.Plan, keys: tuple[Key[Any], ...]) -> AsyncIterator[Settings]:
    """The `config` port the overlay binds, over this system's declared keys (A6)."""
    wired = {"config": {"keys": keys}}
    async with adapters.compose(planned, hooks=wired, ports=("secrets", "config")) as built:
        yield built["config"]


@asynccontextmanager
async def approval_records(url: str) -> AsyncIterator[ApprovalRecordReader]:
    """The agent's approval records, read through the harness's records port on
    a connection of this system's own that cannot write (A3, A4)."""
    from agent_harness.state.postgres import pool
    from agent_harness.state.records import PostgresRecords

    read_only = make_conninfo(url, options="-c default_transaction_read_only=on")
    async with pool(read_only, min_size=1, max_size=2) as opened:
        yield PostgresRecords(opened)


def world_records(path: Path = WORLD) -> dict[str, list[dict[str, Any]]]:
    records: dict[str, list[dict[str, Any]]] = yaml.safe_load(path.read_text())["records"]
    return records


async def _seed(url: str, fresh: bool, if_empty: bool = False) -> None:
    if if_empty and not await st.is_empty(url):
        print("  seed skipped: the claims system already holds data")
        return
    if fresh:
        await st.reset(url)
    written = await st.seed(url, world_records(Path(env("CLAIMS_WORLD", str(WORLD)))))
    print(f"  seeded {written} rows from the FNOL world{' (fresh)' if fresh else ''}")


def _serve(port: int, host: str) -> None:
    import uvicorn

    from claims_system import server

    url = env("CLAIMS_DATABASE_URL")
    records_url = env("CLAIMS_RECORDS_DATABASE_URL", "")
    planned = adapters.plan(overlay())

    async def run() -> None:
        async with (
            st.Store.open(url) as store,
            _records(records_url) as approvals,
            authorisation(planned) as authorise,
            settings(planned, server.KEYS) as limits,
        ):
            mcp = server.build(store, authorise=authorise, approvals=approvals, limits=limits)
            app = mcp.streamable_http_app(host=host)
            config = uvicorn.Config(app, host=host, port=port, log_level="warning")
            print(
                f"  claims system on http://{host}:{port}/mcp, callers checked by "
                f"{planned.adapter('authorise')} ({planned.environment}); automatic payout "
                f"limit {limits.get(server.AUTOMATIC_LIMIT)} from {planned.adapter('config')}"
            )
            await uvicorn.Server(config).serve()

    asyncio.run(run())


@asynccontextmanager
async def _records(url: str) -> AsyncIterator[ApprovalRecordReader | None]:
    """No URL, no records: every payout is refused for want of an approval."""
    if not url:
        yield None
        return
    async with approval_records(url) as records:
        yield records


def main() -> None:
    parser = argparse.ArgumentParser(prog="claims_system")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("migrate")
    seeding = sub.add_parser("seed")
    seeding.add_argument("--fresh", action="store_true", help="drop claims the demo made")
    seeding.add_argument(
        "--if-empty", action="store_true", help="seed only a database never seeded (deployed)"
    )
    serving = sub.add_parser("serve")
    serving.add_argument("--port", type=int, default=9050)
    serving.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()
    if args.command == "migrate":
        applied = asyncio.run(st.migrate(env("CLAIMS_DATABASE_URL")))
        print(f"  migrations applied: {', '.join(applied) or 'none (up to date)'}")
    elif args.command == "seed":
        asyncio.run(_seed(env("CLAIMS_DATABASE_URL"), args.fresh, args.if_empty))
    else:
        _serve(args.port, args.host)


if __name__ == "__main__":
    main()
