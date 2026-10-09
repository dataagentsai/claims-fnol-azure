"""The claims system's rows, on PostgreSQL: the AOAS operations against real tables.

Every answer has the shape the agent already reads from the AgentTwin world, so
the agent cannot tell the two apart — only the URL changes (Tier 2):

    read, row visible          {found: True, **row}
    listing                    {found: True, items: [the session's rows]}
    write allowed              {allowed: True, reason: "allowed", **row}
    write refused              {allowed: False, reason, **row}, plus `kind` when
                               the refusal is `declined` or `transient`
    unknown, or not yours      {found: False, allowed: False, reason: "no <entity> <id>"}

**The rules are restated here, not imported** (the order system's reason): the
AOAS says when a claim may be withdrawn or paid; the agent reads that spec, and
so does this system, independently. Two statements of one rule disagree loudly.

**Ownership is every query's `WHERE`**: a row that is not the session's
policyholder's is never read, so a stranger's claim reads exactly like a missing
one (P-OWNERSHIP, F-016).

**A keyed write is answered once**: the first answer is stored beside the effect,
in the same transaction, and a repeated key reads it back (AOAS idempotency).
"""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from importlib import resources
from typing import Any

import psycopg
from psycopg import errors as pg_errors
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

Row = dict[str, Any]
Conn = psycopg.AsyncConnection[Row]

OPEN_STATUSES = ("registered", "documents_pending", "under_assessment", "approved")
"""A claim not yet withdrawn, rejected or paid: the same incident reported again
on its policy is this claim (`register_claim.identity`)."""
TAKES_DOCUMENTS = ("registered", "documents_pending")
WITHDRAWABLE = ("registered", "documents_pending")
PAYABLE = ("approved",)

REFUSALS = {
    "register_claim": "a policy that is {status} cannot take a new claim",
    "submit_document": "a claim that is {status} is no longer taking documents",
    "withdraw_claim": "a claim that is {status} can no longer be withdrawn here",
    "issue_payout": "a claim that is {status} cannot be paid",
}
"""This system's own words for a refused precondition — the world's `presents`."""

DECLINED_ACCOUNT = "the account on policy {policy} can no longer receive a payment"
TRANSIENT = "the claims system is busy with this record; asking again may work"

CLAIM_COLUMNS = (
    "id, policy_id, policyholder_id, status, incident_type, "
    "(current_date - incident_on) AS incident_days_ago, injuries, documents_missing, "
    "approved_amount, note"
)
POLICY_COLUMNS = "id, policyholder_id, product, status, vehicle_reg, excess, payout_account_last4"


def unknown(entity: str, key: str) -> Row:
    """Built from the key alone, so not-yours and not-there read the same."""
    return {"found": False, "allowed": False, "reason": f"no {entity} {key}"}


def allowed(row: Row, **extra: Any) -> Row:
    return {"allowed": True, "reason": "allowed", **row, **extra}


def refused(operation: str, row: Row, *, kind: str | None = None, reason: str = "") -> Row:
    said = reason or REFUSALS[operation].format(status=row.get("status"))
    return {"allowed": False, "reason": said, **row, **({"kind": kind} if kind else {})}


def migration_files() -> list[tuple[str, str]]:
    """The migrations, in order, as `(name, sql)`."""
    folder = resources.files("claims_system") / "migrations"
    found = sorted(p for p in folder.iterdir() if p.name.endswith(".sql"))
    return [(p.name, p.read_text()) for p in found]


async def migrate(url: str) -> list[str]:
    """Apply every migration not yet applied, each in its own transaction.
    Returns what it applied."""
    applied: list[str] = []
    async with await psycopg.AsyncConnection.connect(url, autocommit=True) as conn:
        await conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations "
            "(name text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"
        )
        for name, sql in migration_files():
            async with conn.transaction():
                done = await conn.execute(
                    "SELECT 1 FROM schema_migrations WHERE name = %s", (name,)
                )
                if await done.fetchone() is not None:
                    continue
                await conn.execute(sql.encode())
                await conn.execute("INSERT INTO schema_migrations (name) VALUES (%s)", (name,))
            applied.append(name)
    return applied


Write = Callable[[Conn, Row], Awaitable[Row]]


@dataclass
class Store:
    """The eight claims-system operations of the AOAS (`external.claims_system`)."""

    pool: AsyncConnectionPool[Conn]

    @classmethod
    @asynccontextmanager
    async def open(cls, url: str, *, max_size: int = 4) -> AsyncIterator[Store]:
        pool: AsyncConnectionPool[Conn] = AsyncConnectionPool(
            url,
            min_size=1,
            max_size=max_size,
            open=False,
            kwargs={"row_factory": dict_row, "autocommit": False},
        )
        await pool.open(wait=True)
        try:
            yield cls(pool)
        finally:
            await pool.close()

    # ----------------------------------------------------------------- reads

    async def get_policy(self, holder: str | None, id: str) -> Row:
        row = await self._one(f"SELECT {POLICY_COLUMNS} FROM policy", id, holder)
        return {"found": True, **row} if row else unknown("policy", id)

    async def list_policies(self, holder: str | None) -> Row:
        return await self._many(f"SELECT {POLICY_COLUMNS} FROM policy", holder)

    async def get_claim(self, holder: str | None, id: str) -> Row:
        row = await self._one(f"SELECT {CLAIM_COLUMNS} FROM claim", id, holder)
        return {"found": True, **row} if row else unknown("claim", id)

    async def list_claims(self, holder: str | None) -> Row:
        return await self._many(f"SELECT {CLAIM_COLUMNS} FROM claim", holder)

    async def _one(self, select: str, id: str, holder: str | None) -> Row | None:
        if holder is None:
            return None
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                f"{select} WHERE id = %s AND policyholder_id = %s", (id, holder)
            )
            return await cur.fetchone()

    async def _many(self, select: str, holder: str | None) -> Row:
        if holder is None:
            return {"found": True, "items": []}  # no session sees nothing, never everything
        async with self.pool.connection() as conn:
            cur = await conn.execute(f"{select} WHERE policyholder_id = %s ORDER BY id", (holder,))
            return {"found": True, "items": await cur.fetchall()}

    # ---------------------------------------------------------------- writes

    async def register_claim(
        self, holder: str | None, id: str, incident_type: str, *, key: str | None = None
    ) -> Row:
        """FNOL: a new claim on an active policy, or the open claim this incident
        already is. Decides nothing about cover."""

        async def act(conn: Conn, policy: Row) -> Row:
            if policy["status"] != "active":
                return refused("register_claim", policy)
            same = await _open_claim(conn, policy["id"], incident_type)
            made = same or await _new_claim(conn, policy, incident_type)
            return allowed(policy, created={"entity": "claim", "id": made})

        return await self._write("register_claim", "policy", holder, id, key, act)

    async def submit_document(
        self, holder: str | None, id: str, document_type: str, *, key: str | None = None
    ) -> Row:
        """Attach a document; the count of missing ones goes down by one. A type
        already attached is the same document (`identity: [claim_id, document_type]`)."""

        async def act(conn: Conn, claim: Row) -> Row:
            if claim["status"] not in TAKES_DOCUMENTS:
                return refused("submit_document", claim)
            added = await conn.execute(
                "INSERT INTO claim_document (claim_id, document_type) VALUES (%s, %s) "
                "ON CONFLICT DO NOTHING",
                (claim["id"], document_type.strip().lower()),
            )
            if added.rowcount:
                await conn.execute(_ONE_FEWER, (claim["id"],))
            return allowed(await _claim(conn, claim["id"]))

        return await self._write("submit_document", "claim", holder, id, key, act)

    async def withdraw_claim(self, holder: str | None, id: str, *, key: str | None = None) -> Row:
        async def act(conn: Conn, claim: Row) -> Row:
            if claim["status"] not in WITHDRAWABLE:
                return refused("withdraw_claim", claim)
            await conn.execute("UPDATE claim SET status = 'withdrawn' WHERE id = %s", (id,))
            return allowed(await _claim(conn, id))

        return await self._write("withdraw_claim", "claim", holder, id, key, act)

    async def issue_payout(self, holder: str | None, id: str, *, key: str | None = None) -> Row:
        """Pay an approved claim its approved amount — never an amount anybody
        states — to the account on its policy. Who may ask is the server's check;
        this is what the row allows."""

        async def act(conn: Conn, claim: Row) -> Row:
            if claim["status"] not in PAYABLE:
                return refused("issue_payout", claim)
            account = await conn.execute(
                "SELECT payout_account_open FROM policy WHERE id = %s", (claim["policy_id"],)
            )
            found = await account.fetchone()
            if not (found and found["payout_account_open"]):
                reason = DECLINED_ACCOUNT.format(policy=claim["policy_id"])
                return refused("issue_payout", claim, kind="declined", reason=reason)
            await conn.execute("UPDATE claim SET status = 'paid' WHERE id = %s", (id,))
            return allowed(await _claim(conn, id))

        return await self._write("issue_payout", "claim", holder, id, key, act)

    async def _write(
        self,
        operation: str,
        entity: str,
        holder: str | None,
        id: str,
        key: str | None,
        act: Write,
    ) -> Row:
        """One write: the row locked and owned, the key looked up, the effect and
        its answer committed together. A row another writer holds is `transient`."""
        if holder is None:
            return unknown(entity, id)
        try:
            async with self.pool.connection() as conn, conn.transaction():
                await conn.execute("SET LOCAL lock_timeout = '2s'")
                seen = await _answered(conn, holder, key)
                if seen is not None:
                    return seen
                row = await _locked(conn, entity, id, holder)
                if row is None:
                    return unknown(entity, id)
                answer = await act(conn, row)
                await _remember(conn, holder, key, operation, answer)
                return answer
        except (pg_errors.LockNotAvailable, pg_errors.SerializationFailure):
            return {"allowed": False, "reason": TRANSIENT, "kind": "transient", "id": id}


_ONE_FEWER = (
    "UPDATE claim SET documents_missing = greatest(documents_missing - 1, 0), "
    # The AOAS invariant: documents_pending means at least one is missing. The
    # last one received is what the assessor was waiting for, so assessment
    # starts — a transition the AOAS gives to the assessor (FINDINGS F-19).
    "status = CASE WHEN status = 'documents_pending' AND documents_missing <= 1 "
    "THEN 'under_assessment' ELSE status END WHERE id = %s"
)


async def _answered(conn: Conn, holder: str, key: str | None) -> Row | None:
    if key is None:
        return None
    cur = await conn.execute(
        "SELECT answer FROM answered WHERE policyholder_id = %s AND key = %s", (holder, key)
    )
    found = await cur.fetchone()
    return dict(found["answer"]) if found else None


async def _remember(conn: Conn, holder: str, key: str | None, operation: str, answer: Row) -> None:
    if key is None:
        return
    await conn.execute(
        "INSERT INTO answered (policyholder_id, key, operation, answer) VALUES (%s, %s, %s, %s)",
        (holder, key, operation, json.dumps(answer)),
    )


async def _locked(conn: Conn, entity: str, id: str, holder: str) -> Row | None:
    columns = CLAIM_COLUMNS if entity == "claim" else POLICY_COLUMNS
    cur = await conn.execute(
        f"SELECT {columns} FROM {entity} WHERE id = %s AND policyholder_id = %s FOR UPDATE",
        (id, holder),
    )
    return await cur.fetchone()


async def _claim(conn: Conn, id: str) -> Row:
    """The claim as it is now, read back rather than assumed."""
    cur = await conn.execute(f"SELECT {CLAIM_COLUMNS} FROM claim WHERE id = %s", (id,))
    found = await cur.fetchone()
    assert found is not None
    return found


async def _open_claim(conn: Conn, policy: str, incident_type: str) -> str | None:
    cur = await conn.execute(
        "SELECT id FROM claim WHERE policy_id = %s AND incident_type = %s "
        "AND status = ANY(%s) ORDER BY id LIMIT 1",
        (policy, incident_type, list(OPEN_STATUSES)),
    )
    found = await cur.fetchone()
    return str(found["id"]) if found else None


async def _new_claim(conn: Conn, policy: Row, incident_type: str) -> str:
    cur = await conn.execute(
        "INSERT INTO claim (id, policy_id, policyholder_id, status, incident_type, incident_on) "
        "VALUES ('CLM-' || lpad(nextval('claim_number')::text, 6, '0'), %s, %s, "
        "'registered', %s, current_date) RETURNING id",
        (policy["id"], policy["policyholder_id"], incident_type),
    )
    made = await cur.fetchone()
    assert made is not None
    return str(made["id"])


# ------------------------------------------------------------------- seeding


async def seed(url: str, records: dict[str, list[Row]], *, today: dt.date | None = None) -> int:
    """Load the world's rows (gates/motor-claims-fnol/worlds) into empty tables,
    or into tables already holding them: a row that exists is replaced, so
    seeding again resets the demo. Returns the rows written."""
    day = today or dt.date.today()
    written = 0
    async with await psycopg.AsyncConnection.connect(url) as conn, conn.transaction():
        for row in records.get("policyholder", []):
            await conn.execute(_UPSERT_HOLDER, row)
            written += 1
        for row in records.get("policy", []):
            await conn.execute(_UPSERT_POLICY, {"payout_account_open": True, **row})
            written += 1
        for row in records.get("claim", []):
            on = day - dt.timedelta(days=int(row.get("incident_days_ago", 0)))
            await conn.execute(_UPSERT_CLAIM, {**row, "incident_on": on})
            written += 1
        await conn.execute(
            "SELECT setval('claim_number', greatest(1, coalesce(max(substr(id, 5)::int), 0))) "
            "FROM claim"
        )
    return written


_UPSERT_HOLDER = (
    "INSERT INTO policyholder (id, name, email, phone) "
    "VALUES (%(id)s, %(name)s, %(email)s, %(phone)s) ON CONFLICT (id) DO UPDATE SET "
    "name = excluded.name, email = excluded.email, phone = excluded.phone"
)
_UPSERT_POLICY = (
    "INSERT INTO policy (id, policyholder_id, product, status, vehicle_reg, excess, "
    "payout_account_last4, payout_account_open) VALUES (%(id)s, %(policyholder_id)s, "
    "%(product)s, %(status)s, %(vehicle_reg)s, %(excess)s, %(payout_account_last4)s, "
    "%(payout_account_open)s) ON CONFLICT (id) DO UPDATE SET status = excluded.status, "
    "product = excluded.product, payout_account_open = excluded.payout_account_open"
)
_UPSERT_CLAIM = (
    "INSERT INTO claim (id, policy_id, policyholder_id, status, incident_type, incident_on, "
    "injuries, documents_missing, approved_amount, note) VALUES (%(id)s, %(policy_id)s, "
    "%(policyholder_id)s, %(status)s, %(incident_type)s, %(incident_on)s, %(injuries)s, "
    "%(documents_missing)s, %(approved_amount)s, %(note)s) ON CONFLICT (id) DO UPDATE SET "
    "status = excluded.status, incident_on = excluded.incident_on, "
    "documents_missing = excluded.documents_missing, approved_amount = excluded.approved_amount"
)


async def is_empty(url: str) -> bool:
    """No policyholder yet: a database the seed has never written to. A deployed
    claims system seeds only then, so a replica waking from zero keeps every claim
    and decision made since (owner's decision, 9 Oct 2026; FINDINGS F-45)."""
    async with await psycopg.AsyncConnection.connect(url) as conn:
        row = await (
            await conn.execute("SELECT NOT EXISTS (SELECT 1 FROM policyholder)")
        ).fetchone()
    return bool(row and row[0])


async def reset(url: str) -> None:
    """Remove every claim the demo made and every remembered answer, leaving the
    seeded rows to be written back by `seed`."""
    async with await psycopg.AsyncConnection.connect(url) as conn, conn.transaction():
        await conn.execute("DELETE FROM answered")
        await conn.execute("DELETE FROM claim_document")
        await conn.execute("DELETE FROM claim")


__all__ = [
    "DECLINED_ACCOUNT",
    "is_empty",
    "REFUSALS",
    "TRANSIENT",
    "Store",
    "migrate",
    "migration_files",
    "reset",
    "seed",
    "unknown",
]
