"""The A4 database logins on this Mac, for scripts/dev-up.sh. Idempotent.

1. The admin login (`CLAIMS_ADMIN_URL`, the `claims_fnol` role, no database in
   the URL) makes databases and roles and nothing else; no app runs as it. A
   setup from before A4 has no such line: it is taken from
   `CLAIMS_TEST_SERVER_URL`, the same login. It needs CREATEROLE, given once
   here through the local superuser (the Mac user, as the README's setup does).
2. Each role's password is made once and kept only in `.env` (gitignored, mode
   600), inside the URLs the apps read: the agent's
   (`CLAIMS_DBOS_DATABASE_URL`, claims_agent) and the claims system's
   (`CLAIMS_DATABASE_URL` and `CLAIMS_RECORDS_DATABASE_URL`, claims_system).
3. The two databases are made if missing, then infra/sql/roles.sql runs as the
   admin: the roles, the ownership hand-over, the grants.

Prints role and database names only, never a password or a URL.
"""

from __future__ import annotations

import os
import secrets
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import psycopg

ROOT = Path(__file__).resolve().parents[1]
DOTENV = ROOT / ".env"
ROLES_SQL = ROOT / "infra" / "sql" / "roles.sql"
AGENT_ROLE, SYSTEM_ROLE = "claims_agent", "claims_system"
CLAIMS_DB, AGENT_DB = "claims_fnol", "claims_fnol_dbos"


def read(lines: list[str]) -> dict[str, str]:
    found = {}
    for line in lines:
        key, sep, value = line.partition("=")
        if sep and not key.lstrip().startswith("#"):
            found[key.strip()] = value.strip()
    return found


def write(lines: list[str], updates: dict[str, str]) -> None:
    """Replace each key's line in place, append the missing ones; mode 600."""
    out, seen = [], set()
    for line in lines:
        key = line.partition("=")[0].strip()
        if key in updates and not key.startswith("#"):
            out.append(f"{key}={updates[key]}")
            seen.add(key)
        else:
            out.append(line)
    missing = [k for k in updates if k not in seen]
    if missing:
        out.append("# A4 (scripts/dev_roles.py): one login per app; the admin only makes them.")
        out.extend(f"{k}={updates[k]}" for k in missing)
    tmp = DOTENV.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write("\n".join(out) + "\n")
    tmp.replace(DOTENV)


def url(base: str, user: str, password: str, database: str = "") -> str:
    parts = urlsplit(base)
    netloc = f"{user}:{password}@{parts.hostname}" + (f":{parts.port}" if parts.port else "")
    return urlunsplit((parts.scheme, netloc, f"/{database}" if database else "", "", ""))


def password_of(env: dict[str, str], key: str, user: str) -> str:
    """The role's password already in .env, or a new one."""
    parts = urlsplit(env.get(key, ""))
    if parts.username == user and parts.password:
        return parts.password
    return secrets.token_urlsafe(24)


def admin_url(env: dict[str, str]) -> str:
    for key in ("CLAIMS_ADMIN_URL", "CLAIMS_TEST_SERVER_URL"):
        if env.get(key):
            return env[key].rstrip("/")
    old = urlsplit(env.get("CLAIMS_DATABASE_URL", ""))
    if old.username and old.username not in (AGENT_ROLE, SYSTEM_ROLE):
        return urlunsplit((old.scheme, old.netloc, "", "", ""))
    sys.exit("No admin login in .env: set CLAIMS_ADMIN_URL (see .env.example).")


def can_create_roles(admin: str) -> None:
    with psycopg.connect(f"{admin}/postgres", autocommit=True) as c:
        row = c.execute(
            "SELECT rolcreaterole FROM pg_roles WHERE rolname = current_user"
        ).fetchone()
    if row and row[0]:
        return
    name, parts = urlsplit(admin).username, urlsplit(admin)
    print(f"  giving {name} CREATEROLE (once), as this Mac's PostgreSQL superuser")
    given = subprocess.run(
        [
            "psql",
            "-X",
            "-q",
            "-h",
            str(parts.hostname),
            "-p",
            str(parts.port or 5432),
            "-d",
            "postgres",
            "-c",
            f'ALTER ROLE "{name}" CREATEROLE',
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if given.returncode:
        sys.exit(
            f"  could not: {given.stderr.strip()}\n  run as a superuser: "
            f"psql -d postgres -c 'ALTER ROLE \"{name}\" CREATEROLE'"
        )


def databases(admin: str) -> None:
    with psycopg.connect(f"{admin}/postgres", autocommit=True) as c:
        for name in (CLAIMS_DB, AGENT_DB):
            if (
                c.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,)).fetchone()
                is None
            ):
                c.execute(f'CREATE DATABASE "{name}"'.encode())
                print(f"  database {name} made")


def roles_sql(admin: str, agent_password: str, system_password: str) -> None:
    parts = urlsplit(admin)
    env = {
        **os.environ,
        "PGHOST": str(parts.hostname),
        "PGPORT": str(parts.port or 5432),
        "PGUSER": parts.username or "",
        "PGPASSWORD": parts.password or "",
        "PGDATABASE": "postgres",
        "A4_AGENT_PASSWORD": agent_password,
        "A4_SYSTEM_PASSWORD": system_password,
    }
    names = {
        "agent_role": AGENT_ROLE,
        "system_role": SYSTEM_ROLE,
        "agent_db": AGENT_DB,
        "claims_db": CLAIMS_DB,
    }
    command = ["psql", "-X", "-q", *(f"-v{k}={v}" for k, v in names.items()), "-f", str(ROLES_SQL)]
    done = subprocess.run(command, env=env, capture_output=True, text=True, check=False)
    if done.returncode:
        sys.exit(f"  infra/sql/roles.sql failed: {done.stderr.strip()}")


def main() -> None:
    lines = DOTENV.read_text().splitlines()
    env = read(lines)
    admin = admin_url(env)
    agent_password = password_of(env, "CLAIMS_DBOS_DATABASE_URL", AGENT_ROLE)
    system_password = password_of(env, "CLAIMS_DATABASE_URL", SYSTEM_ROLE)
    can_create_roles(admin)
    databases(admin)
    roles_sql(admin, agent_password, system_password)
    write(
        lines,
        {
            "CLAIMS_ADMIN_URL": admin,
            "CLAIMS_DATABASE_URL": url(admin, SYSTEM_ROLE, system_password, CLAIMS_DB),
            "CLAIMS_RECORDS_DATABASE_URL": url(admin, SYSTEM_ROLE, system_password, AGENT_DB),
            "CLAIMS_DBOS_DATABASE_URL": url(admin, AGENT_ROLE, agent_password, AGENT_DB),
        },
    )
    print(
        f"  logins: {AGENT_ROLE} owns {AGENT_DB}; {SYSTEM_ROLE} owns {CLAIMS_DB} and reads "
        f"{AGENT_DB}'s agent_state.approvals only (passwords in .env)"
    )


if __name__ == "__main__":
    main()
