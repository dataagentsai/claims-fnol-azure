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
import secrets
import shutil
import subprocess
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

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


# ------------------------------------------------------------------ A4: the roles
ROLES_SQL = ROOT / "infra" / "sql" / "roles.sql"


@dataclass(frozen=True)
class Roles:
    """Two throwaway databases under the A4 logins, as dev-up and Azure set them."""

    agent_role: str
    system_role: str
    agent_url: str  # the agent role, the agent's database (state, records, DBOS)
    claims_url: str  # the system role, the claims database
    records_url: str  # the system role, the agent's database (approvals only)


def _as(url: str, user: str, password: str, database: str = "") -> str:
    parts = urlsplit(url)
    host = parts.hostname or "127.0.0.1"
    netloc = f"{user}:{password}@{host}" + (f":{parts.port}" if parts.port else "")
    return urlunsplit((parts.scheme, netloc, f"/{database}" if database else "", "", ""))


def apply_roles(server: str, *, agent_db: str, claims_db: str, roles: dict[str, str]) -> None:
    """Run infra/sql/roles.sql as the server's (admin) role, as dev-up and the
    Azure hook do: names on the command line, passwords in the environment.
    `roles` maps agent_role, system_role, agent_password, system_password."""
    psql = shutil.which("psql")
    if psql is None:
        pytest.skip("psql is not installed")
    admin = urlsplit(server)
    env = {
        **os.environ,
        "PGHOST": admin.hostname or "127.0.0.1",
        "PGPORT": str(admin.port or 5432),
        "PGUSER": admin.username or "",
        "PGPASSWORD": admin.password or "",
        "PGDATABASE": "postgres",
        "A4_AGENT_PASSWORD": roles["agent_password"],
        "A4_SYSTEM_PASSWORD": roles["system_password"],
    }
    names = {
        "agent_role": roles["agent_role"],
        "system_role": roles["system_role"],
        "agent_db": agent_db,
        "claims_db": claims_db,
    }
    command = [psql, "-X", "-q", *(f"-v{k}={v}" for k, v in names.items()), "-f", str(ROLES_SQL)]
    done = subprocess.run(command, env=env, capture_output=True, text=True, check=False)
    assert done.returncode == 0, f"roles.sql failed: {done.stderr.strip()}"


@asynccontextmanager
async def least_privilege(claims_url: str, agent_url: str) -> AsyncIterator[Roles]:
    """The A4 logins over two throwaway databases, under names unique to this
    run; on the way out the roles' objects go back to the admin and the roles are
    dropped, so no role outlives the test (the databases go with `throwaway`)."""
    server = needs_postgres()
    tag = uuid.uuid4().hex[:8]
    roles = {
        "agent_role": f"t_claims_agent_{tag}",
        "system_role": f"t_claims_system_{tag}",
        "agent_password": secrets.token_urlsafe(18),
        "system_password": secrets.token_urlsafe(18),
    }
    agent_db, claims_db = urlsplit(agent_url).path[1:], urlsplit(claims_url).path[1:]
    try:
        apply_roles(server, agent_db=agent_db, claims_db=claims_db, roles=roles)
        agent, system = roles["agent_role"], roles["system_role"]
        yield Roles(
            agent_role=agent,
            system_role=system,
            agent_url=_as(server, agent, roles["agent_password"], agent_db),
            claims_url=_as(server, system, roles["system_password"], claims_db),
            records_url=_as(server, system, roles["system_password"], agent_db),
        )
    finally:
        await _drop_roles(
            server, (agent_db, claims_db), (roles["agent_role"], roles["system_role"])
        )


async def _drop_roles(server: str, databases: tuple[str, ...], roles: tuple[str, str]) -> None:
    async with await psycopg.AsyncConnection.connect(f"{server}/postgres", autocommit=True) as c:
        cur = await c.execute(
            "SELECT rolname FROM pg_roles WHERE rolname = ANY(%s)", (list(roles),)
        )
        made = [str(r[0]) for r in await cur.fetchall()]
    if not made:
        return
    named = ", ".join(f'"{r}"' for r in made)
    for database in databases:
        async with await psycopg.AsyncConnection.connect(
            f"{server}/{database}", autocommit=True
        ) as c:
            await c.execute(f"REASSIGN OWNED BY {named} TO CURRENT_USER")
            await c.execute(f"DROP OWNED BY {named}")
    async with await psycopg.AsyncConnection.connect(f"{server}/postgres", autocommit=True) as c:
        for role in made:
            await c.execute(f'DROP ROLE "{role}"')


__all__ = [
    "Roles",
    "apply_roles",
    "least_privilege",
    "needs_postgres",
    "server_url",
    "throwaway",
]
