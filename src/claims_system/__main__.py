"""`python -m claims_system migrate | seed [--fresh | --if-empty] | serve [--port N]`.

Reads `CLAIMS_DATABASE_URL` (and, to check payouts against the approval wait,
`CLAIMS_DBOS_DATABASE_URL`) from the environment or `.env`.
"""

from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path
from typing import Any

import yaml

from claims_system import store as st

ROOT = Path(__file__).resolve().parents[2]
WORLD = ROOT.parent / "clean-ai-engineering/gates/motor-claims-fnol/worlds/motor-claims-fnol.yaml"


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
    dbos_url = env("CLAIMS_DBOS_DATABASE_URL", "")

    async def run() -> None:
        async with st.Store.open(url) as store:
            approvals = server.dbos_approvals(dbos_url) if dbos_url else None
            mcp = server.build(store, approvals=approvals)
            app = mcp.streamable_http_app(host=host)
            config = uvicorn.Config(app, host=host, port=port, log_level="warning")
            print(f"  claims system on http://{host}:{port}/mcp")
            await uvicorn.Server(config).serve()

    asyncio.run(run())


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
