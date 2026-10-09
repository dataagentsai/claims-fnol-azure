"""`python -m claims_fnol_app [--port 8077]`: the agent app, on this Mac.

Builds the app from the overlay `CLAIMS_FNOL_ENV` names (default `local`:
`config/local.yaml`), whose secrets come from the environment and `.env`. The claims system must
already be serving (`python -m claims_system serve`); `scripts/dev-up.sh`
starts both.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib

import uvicorn
from agent_harness import adapters

from claims_fnol_app.compose import compose, overlay


async def main(host: str, port: int) -> None:
    async with compose(adapters.plan(overlay())) as app:
        print(f"  claims agent on http://{host}:{port}  ·  sign in at http://{host}:{port}/signin")
        config = uvicorn.Config(app, host=host, port=port, log_level="warning")
        await uvicorn.Server(config).serve()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(prog="claims_fnol_app")
    parser.add_argument("--port", type=int, default=8077)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main(args.host, args.port))
