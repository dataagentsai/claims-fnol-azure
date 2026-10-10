"""Tier 4a A4: a database login per app, each with the least it needs.

infra/sql/roles.sql, run as the admin login over two throwaway databases, under
role names unique to this run (dropped after). Each row is one role trying one
action, against PostgreSQL's own privilege checks: no session setting is in the
way, so a refusal here is the role's, not `default_transaction_read_only` (F-51).

Two setups, each held to the whole table:
- **made by the admin**: the apps ran as the admin before A4 (this Mac's existing
  databases), so the script must hand every table, DBOS's included, over;
- **fresh**: the script first, then each app makes its own tables as itself, as
  DBOS does at launch (`--fresh`, a new machine, Azure).
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Iterator
from contextlib import AsyncExitStack
from pathlib import Path

import psycopg
import pytest
import sqlalchemy as sa
from dbos._migration import ensure_dbos_schema, run_dbos_migrations
from pg import ROLES_SQL, Roles, least_privilege, needs_postgres, throwaway

from claims_fnol_app.compose import migrate_agent_state
from claims_system import store as st

ROOT = Path(__file__).resolve().parents[1]


def dbos_migrate(url: str) -> None:
    """What DBOS 3.2 runs at launch (`SystemDatabase.run_migrations`): its schema,
    as whichever login the URL names."""
    engine = sa.create_engine(url.replace("postgresql://", "postgresql+psycopg://", 1))
    try:
        ensure_dbos_schema(engine, "dbos")
        run_dbos_migrations(engine, "dbos", True)
    finally:
        engine.dispose()


async def _made_by_the_admin(stack: AsyncExitStack) -> Roles:
    claims = await stack.enter_async_context(throwaway("a4_claims"))
    agent = await stack.enter_async_context(throwaway("a4_agent"))
    await st.migrate(claims)
    await migrate_agent_state(agent)
    dbos_migrate(agent)
    return await stack.enter_async_context(least_privilege(claims, agent))


async def _fresh(stack: AsyncExitStack) -> Roles:
    claims = await stack.enter_async_context(throwaway("a4_claims"))
    agent = await stack.enter_async_context(throwaway("a4_agent"))
    roles = await stack.enter_async_context(least_privilege(claims, agent))
    await st.migrate(roles.claims_url)
    await migrate_agent_state(roles.agent_url)
    dbos_migrate(roles.agent_url)
    return roles


SETUPS = {"made by the admin": _made_by_the_admin, "fresh": _fresh}


@pytest.fixture(scope="module", params=sorted(SETUPS))
def roles(request: pytest.FixtureRequest) -> Iterator[Roles]:
    needs_postgres()
    loop = asyncio.new_event_loop()
    stack = AsyncExitStack()
    try:
        yield loop.run_until_complete(SETUPS[request.param](stack))
    finally:
        loop.run_until_complete(stack.aclose())
        loop.close()


# --------------------------------------------------------------- what each may do
APPROVAL = (
    "INSERT INTO agent_state.approvals (id, action, args, args_digest, requested_for,"
    " idempotency_key, expires_at, status, created_at) VALUES ('wf-1', 'issue_payout',"
    " '{}', 'd', 'PH-1001', 'k', now(), 'waiting', now())"
)
CLAIM = [
    "INSERT INTO policyholder VALUES ('PH-9', 'T', 't@example.com', '0')",
    "INSERT INTO policy (id, policyholder_id, product, status, vehicle_reg, excess,"
    " payout_account_last4) VALUES ('POL-099999', 'PH-9', 'comprehensive', 'active', 'KA', 0, '1')",
    "INSERT INTO claim (id, policy_id, policyholder_id, status, incident_type, incident_on)"
    " VALUES ('CLM-099999', 'POL-099999', 'PH-9', 'registered', 'collision', current_date)",
]
CHECKPOINT = [
    "INSERT INTO agent_state.checkpoints (run_id, state) VALUES ('r-1', '\\x00')",
    "SELECT state FROM agent_state.checkpoints WHERE run_id = 'r-1'",
    "UPDATE agent_state.checkpoints SET state = '\\x01' WHERE run_id = 'r-1'",
]

# (role, its connection: database it reaches, the action, its SQL, allowed)
TABLE: list[tuple[str, str, str, list[str], bool]] = [
    (
        "system",
        "records",
        "SELECT agent_state.approvals",
        ["SELECT * FROM agent_state.approvals"],
        True,
    ),
    ("system", "records", "INSERT agent_state.approvals", [APPROVAL], False),
    (
        "system",
        "records",
        "UPDATE agent_state.approvals",
        ["UPDATE agent_state.approvals SET reason = 'x'"],
        False,
    ),
    (
        "system",
        "records",
        "DELETE agent_state.approvals",
        ["DELETE FROM agent_state.approvals"],
        False,
    ),
    (
        "system",
        "records",
        "SELECT dbos.workflow_status",
        ["SELECT * FROM dbos.workflow_status"],
        False,
    ),
    (
        "system",
        "records",
        "SELECT agent_state.checkpoints",
        ["SELECT * FROM agent_state.checkpoints"],
        False,
    ),
    (
        "system",
        "records",
        "SELECT agent_state.requests",
        ["SELECT * FROM agent_state.requests"],
        False,
    ),
    (
        "system",
        "records",
        "SELECT agent_state.escalations",
        ["SELECT * FROM agent_state.escalations"],
        False,
    ),
    ("system", "claims", "INSERT into its own claim table", CLAIM, True),
    ("agent", "agent", "read and write agent_state.checkpoints", CHECKPOINT, True),
    ("agent", "agent", "write agent_state.approvals", [APPROVAL], True),
    ("agent", "agent", "SELECT dbos.workflow_status", ["SELECT * FROM dbos.workflow_status"], True),
    ("agent", "claims", "SELECT the claims database's claim", ["SELECT * FROM claim"], False),
]


def attempt(url: str, statements: list[str]) -> bool:
    """True if every statement ran; False if PostgreSQL refused for want of a
    privilege (on the connection or on a statement). Always rolled back."""
    try:
        with psycopg.connect(url) as conn:
            try:
                for sql in statements:
                    conn.execute(sql.encode())
                return True
            finally:
                conn.rollback()
    except psycopg.errors.InsufficientPrivilege:
        return False
    except psycopg.OperationalError as e:
        if "permission denied" in str(e):
            return False
        raise


def url_for(roles: Roles, role: str, reach: str) -> str:
    if role == "system":
        return roles.claims_url if reach == "claims" else roles.records_url
    if reach == "claims":  # the agent role at the claims database: its URL, another database
        return roles.agent_url.rsplit("/", 1)[0] + "/" + roles.claims_url.rsplit("/", 1)[1]
    return roles.agent_url


@pytest.mark.discharges("AHC-0040")
@pytest.mark.parametrize(
    ("role", "reach", "action", "statements", "allowed"),
    TABLE,
    ids=[f"{r[0]}: {r[2]}" for r in TABLE],
)
def test_each_login_may_do_only_its_job(
    roles: Roles, role: str, reach: str, action: str, statements: list[str], allowed: bool
) -> None:
    done = attempt(url_for(roles, role, reach), statements)
    assert done is allowed, f"{role} {action}: {'allowed' if done else 'refused'}"


# ------------------------------------------------------------ who owns what
OWNERS = [
    # (database, schemas, the owner every relation and routine there must have)
    ("agent", ("agent_state", "dbos"), "agent"),
    ("claims", ("public",), "system"),
]


@pytest.mark.discharges("AHC-0040")
@pytest.mark.parametrize(("database", "schemas", "owner"), OWNERS, ids=[o[0] for o in OWNERS])
def test_each_database_and_everything_in_it_is_its_apps(
    roles: Roles, database: str, schemas: tuple[str, ...], owner: str
) -> None:
    admin = needs_postgres()
    name = (roles.agent_url if database == "agent" else roles.claims_url).rsplit("/", 1)[1]
    expected = roles.agent_role if owner == "agent" else roles.system_role
    with psycopg.connect(f"{admin}/{name}") as conn:
        cur = conn.execute(
            "SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname = current_database()"
            " UNION SELECT DISTINCT pg_get_userbyid(c.relowner) FROM pg_class c"
            " JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = ANY(%s)"
            " UNION SELECT DISTINCT pg_get_userbyid(p.proowner) FROM pg_proc p"
            " JOIN pg_namespace n ON n.oid = p.pronamespace WHERE n.nspname = ANY(%s)",
            (list(schemas), list(schemas)),
        )
        owners = {r[0] for r in cur.fetchall()}
    assert owners == {expected}, owners


def test_the_roles_script_makes_every_agent_table_the_app_makes() -> None:
    """roles.sql runs the agent's migrations as the agent role, so the grant on
    approvals has a table on a database no agent has started on; a new
    migration must be listed there too."""
    listed = set(
        re.findall(
            r"^\\ir \.\./\.\./src/claims_fnol_app/migrations/(\S+)$", ROLES_SQL.read_text(), re.M
        )
    )
    files = {p.name for p in (ROOT / "src" / "claims_fnol_app" / "migrations").glob("*.sql")}
    assert listed == files, files ^ listed
