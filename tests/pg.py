"""Throwaway PostgreSQL databases for tests, on the server the owner already runs.

The server is named by `CLAIMS_TEST_SERVER_URL` (environment or `.env`): a role
that may create databases, and no database in the URL. Each database is
created for one test and dropped after it, `WITH (FORCE)`, so nothing is left
behind and no other database on that server is touched. With no URL, the tests
that need one are skipped and say why.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import psycopg
import pytest

ROOT = Path(__file__).resolve().parents[1]


def server_url() -> str | None:
    if os.environ.get("CLAIMS_TEST_SERVER_URL"):
        return os.environ["CLAIMS_TEST_SERVER_URL"].rstrip("/")
    dotenv = ROOT / ".env"
    if dotenv.exists():
        for line in dotenv.read_text().splitlines():
            key, sep, value = line.partition("=")
            if sep and key.strip() == "CLAIMS_TEST_SERVER_URL":
                return value.strip().rstrip("/")
    return None


def needs_postgres() -> str:
    url = server_url()
    if url is None:
        pytest.skip("no PostgreSQL for tests: set CLAIMS_TEST_SERVER_URL (see .env.example)")
    return url


@asynccontextmanager
async def throwaway(prefix: str = "claims_test") -> AsyncIterator[str]:
    """A fresh, empty database's URL; dropped on the way out."""
    server = needs_postgres()
    name = f"{prefix}_{uuid.uuid4().hex[:10]}"
    async with await psycopg.AsyncConnection.connect(f"{server}/postgres", autocommit=True) as c:
        await c.execute(f'CREATE DATABASE "{name}"')
    try:
        yield f"{server}/{name}"
    finally:
        await _drop(server, name)


async def _drop(server: str, name: str, attempts: int = 20) -> None:
    """Drop it, waiting out a backend this role may not end. `WITH (FORCE)` ends
    our own leftover connections; an autovacuum worker on a database just written
    belongs to the server's owner, and this role is not given the power to end
    other roles' sessions on a shared server — so it is waited for instead."""
    async with await psycopg.AsyncConnection.connect(f"{server}/postgres", autocommit=True) as c:
        for attempt in range(attempts):
            try:
                await c.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
                return
            except (psycopg.errors.InsufficientPrivilege, psycopg.errors.ObjectInUse):
                if attempt == attempts - 1:
                    raise
                await asyncio.sleep(0.25)


__all__ = ["needs_postgres", "server_url", "throwaway"]
