"""The claims system on PostgreSQL, through its own MCP server (Tier 2).

Every row here is a call a caller makes over MCP against a throwaway database
seeded with the FNOL world, and what the claims system answers. The shapes are
the AgentTwin world's, so the agent cannot tell the two apart.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from typing import Any

import psycopg
import pytest
import yaml
from agent_harness.contracts import Approval, ApprovalState
from agent_harness.contracts.records import ApprovalRecord, approval_record
from agent_harness.state.postgres import pool
from agent_harness.state.records import InMemoryRecords, PostgresRecords
from kit import AOAS, MEERA, ROHAN
from mcp.client import Client
from pg import throwaway

from claims_fnol.binding import SCOPES as AGENT_SCOPES
from claims_fnol_app.compose import migrate_agent_state
from claims_system import server as srv
from claims_system import store as st
from claims_system.__main__ import approval_records, world_records

NOBODY = None


@asynccontextmanager
async def claims(
    approvals: Mapping[str, ApprovalRecord] | None = None, *, now: float | None = None
) -> AsyncIterator[tuple[Client, str]]:
    """The claims system on a fresh database: an MCP client, and the database URL.
    `approvals` are the agent's records it reads through the records port (A3)."""
    async with throwaway() as url:
        await st.migrate(url)
        await st.seed(url, world_records())
        held = InMemoryRecords()
        for record in (approvals or {}).values():
            await held.put_approval(record)
        async with st.Store.open(url) as store:
            server = srv.build(store, approvals=held, clock=lambda: now or time.time())
            async with Client(server) as client:
                yield client, url


def meta(who: str | None, *, key: str | None = None, grant: str | None = None) -> dict[str, Any]:
    """What the harness's MCP client sends: the session, and a key and grant if any."""
    sent: dict[str, Any] = {} if who is None else {srv.SESSION_META: {"customer_id": who}}
    if key is not None:
        sent[srv.IDEMPOTENCY_META] = key
    if grant is not None:
        sent[srv.APPROVAL_META] = grant
    return sent


async def call(client: Client, tool: str, args: dict[str, Any], **sent: Any) -> Any:
    return await client.call_tool(tool, args, meta=meta(**sent))  # type: ignore[arg-type]


def subset(said: Mapping[str, Any], expected: Mapping[str, Any]) -> dict[str, Any]:
    return {k: said.get(k) for k in expected}


# --------------------------------------------------------------------- reads

READS = [
    # (case, who, tool, args, expected fields of the answer)
    (
        "own claim",
        ROHAN,
        "get_claim",
        {"id": "CLM-010001"},
        {"found": True, "status": "approved", "approved_amount": 8750, "incident_days_ago": 12},
    ),
    (
        "own policy",
        ROHAN,
        "get_policy",
        {"id": "POL-010002"},
        {"found": True, "status": "lapsed", "product": "third_party"},
    ),
    (
        "a stranger's claim reads as missing",
        ROHAN,
        "get_claim",
        {"id": "CLM-019001"},
        {"found": False, "allowed": False, "reason": "no claim CLM-019001"},
    ),
    (
        "a missing claim",
        ROHAN,
        "get_claim",
        {"id": "CLM-999999"},
        {"found": False, "allowed": False, "reason": "no claim CLM-999999"},
    ),
    (
        "a stranger's policy reads as missing",
        ROHAN,
        "get_policy",
        {"id": "POL-019001"},
        {"found": False, "reason": "no policy POL-019001"},
    ),
    (
        "the stranger's own claim",
        MEERA,
        "get_claim",
        {"id": "CLM-019001"},
        {"found": True, "approved_amount": 19600},
    ),
    (
        "no session sees nothing",
        NOBODY,
        "get_claim",
        {"id": "CLM-010001"},
        {"found": False, "reason": "no claim CLM-010001"},
    ),
]


@pytest.mark.parametrize(
    ("case", "who", "tool", "args", "expected"), READS, ids=[r[0] for r in READS]
)
async def test_a_read_answers_for_the_sessions_own_rows_only(
    case: str, who: str | None, tool: str, args: dict[str, Any], expected: dict[str, Any]
) -> None:
    async with claims() as (client, _):
        said = (await call(client, tool, args, who=who)).structured_content
    assert subset(said, expected) == expected, case


LISTINGS = [
    # (who, tool, how many rows, every row's holder)
    (ROHAN, "list_claims", 8, ROHAN),
    (MEERA, "list_claims", 1, MEERA),
    (NOBODY, "list_claims", 0, None),
    (ROHAN, "list_policies", 4, ROHAN),
    (MEERA, "list_policies", 1, MEERA),
]


@pytest.mark.parametrize(("who", "tool", "count", "holder"), LISTINGS)
async def test_a_listing_holds_the_sessions_rows_and_nobody_elses(
    who: str | None, tool: str, count: int, holder: str | None
) -> None:
    async with claims() as (client, _):
        items = (await call(client, tool, {}, who=who)).structured_content["items"]
    assert len(items) == count
    assert {i["policyholder_id"] for i in items} <= {holder}


# -------------------------------------------------------------------- writes

WRITES = [
    # (case, who, tool, args, expected fields of the answer)
    (
        "FNOL on an active policy",
        ROHAN,
        "register_claim",
        {"id": "POL-010001", "incident_type": "collision"},
        {"allowed": True, "created": {"entity": "claim", "id": "CLM-019002"}},
    ),
    (
        "FNOL on a lapsed policy",
        ROHAN,
        "register_claim",
        {"id": "POL-010002", "incident_type": "collision"},
        {"allowed": False, "reason": "a policy that is lapsed cannot take a new claim"},
    ),
    (
        "FNOL on a cancelled policy",
        ROHAN,
        "register_claim",
        {"id": "POL-010003", "incident_type": "theft"},
        {"allowed": False, "reason": "a policy that is cancelled cannot take a new claim"},
    ),
    (
        "FNOL on a stranger's policy",
        ROHAN,
        "register_claim",
        {"id": "POL-019001", "incident_type": "collision"},
        {"found": False, "allowed": False, "reason": "no policy POL-019001"},
    ),
    (
        "the same incident again is the open claim",
        ROHAN,
        "register_claim",
        {"id": "POL-010001", "incident_type": "glass"},
        {"allowed": True, "created": {"entity": "claim", "id": "CLM-010001"}},
    ),
    (
        "a document the assessor waits for",
        ROHAN,
        "submit_document",
        {"id": "CLM-010006", "document_type": "FIR copy"},
        {"allowed": True, "status": "documents_pending", "documents_missing": 1},
    ),
    (
        "a document once assessment started",
        ROHAN,
        "submit_document",
        {"id": "CLM-010007", "document_type": "photos"},
        {
            "allowed": False,
            "reason": "a claim that is under_assessment is no longer taking documents",
        },
    ),
    (
        "withdraw before assessment",
        ROHAN,
        "withdraw_claim",
        {"id": "CLM-010005"},
        {"allowed": True, "status": "withdrawn"},
    ),
    (
        "withdraw once assessed",
        ROHAN,
        "withdraw_claim",
        {"id": "CLM-010007"},
        {
            "allowed": False,
            "reason": "a claim that is under_assessment can no longer be withdrawn here",
        },
    ),
    (
        "withdraw a stranger's claim",
        ROHAN,
        "withdraw_claim",
        {"id": "CLM-019001"},
        {"found": False, "reason": "no claim CLM-019001"},
    ),
    (
        "withdraw with no session",
        NOBODY,
        "withdraw_claim",
        {"id": "CLM-010005"},
        {"found": False, "reason": "no claim CLM-010005"},
    ),
]


@pytest.mark.parametrize(
    ("case", "who", "tool", "args", "expected"), WRITES, ids=[w[0] for w in WRITES]
)
async def test_a_write_lands_only_where_its_precondition_holds(
    case: str, who: str | None, tool: str, args: dict[str, Any], expected: dict[str, Any]
) -> None:
    async with claims() as (client, _):
        said = (await call(client, tool, args, who=who)).structured_content
    assert subset(said, expected) == expected, case


async def test_the_last_document_starts_the_assessment() -> None:
    """documents_pending means at least one is missing (AOAS invariant), so the
    last one received moves the claim on (FINDINGS F-19)."""
    async with claims() as (client, _):
        await call(
            client, "submit_document", {"id": "CLM-010006", "document_type": "fir"}, who=ROHAN
        )
        again = await call(
            client, "submit_document", {"id": "CLM-010006", "document_type": "fir"}, who=ROHAN
        )
        last = await call(
            client, "submit_document", {"id": "CLM-010006", "document_type": "estimate"}, who=ROHAN
        )
    assert again.structured_content["documents_missing"] == 1, "the same type twice is one document"
    assert subset(
        last.structured_content, {"status": "under_assessment", "documents_missing": 0}
    ) == {"status": "under_assessment", "documents_missing": 0}


# --------------------------------------------------------------- idempotency

KEYS = [
    # (case, first caller and key, second caller and key, same answer, claims made)
    ("the same key is the first answer", (ROHAN, "k1"), (ROHAN, "k1"), True, 1),
    ("another key is another request, the same incident", (ROHAN, "k1"), (ROHAN, "k2"), True, 1),
    ("no key twice is the same incident", (ROHAN, None), (ROHAN, None), True, 1),
]


@pytest.mark.parametrize(
    ("case", "first", "second", "same", "made"), KEYS, ids=[k[0] for k in KEYS]
)
async def test_a_repeated_registration_is_one_claim(
    case: str, first: tuple[str, str | None], second: tuple[str, str | None], same: bool, made: int
) -> None:
    args = {"id": "POL-010001", "incident_type": "flood"}
    async with claims() as (client, url):
        one = await call(client, "register_claim", args, who=first[0], key=first[1])
        two = await call(client, "register_claim", args, who=second[0], key=second[1])
        async with await psycopg.AsyncConnection.connect(url) as conn:
            cur = await conn.execute("SELECT count(*) FROM claim WHERE incident_type = 'flood'")
            row = await cur.fetchone()
    assert (one.structured_content == two.structured_content) is same, case
    assert row is not None and row[0] - 1 == made  # the seed holds one flood claim


async def test_a_key_answers_with_its_first_answer_even_after_the_row_moved() -> None:
    """A withdraw retried under its key reads its first answer, not "already withdrawn"."""
    async with claims() as (client, _):
        first = await call(client, "withdraw_claim", {"id": "CLM-010005"}, who=ROHAN, key="w1")
        retry = await call(client, "withdraw_claim", {"id": "CLM-010005"}, who=ROHAN, key="w1")
        fresh = await call(client, "withdraw_claim", {"id": "CLM-010005"}, who=ROHAN, key="w2")
    assert retry.structured_content == first.structured_content
    assert first.structured_content["allowed"] is True
    assert fresh.structured_content["allowed"] is False


async def test_one_callers_key_never_reads_anothers_answer() -> None:
    async with claims() as (client, _):
        await call(client, "withdraw_claim", {"id": "CLM-010005"}, who=ROHAN, key="shared")
        theirs = await call(client, "withdraw_claim", {"id": "CLM-010005"}, who=MEERA, key="shared")
    assert theirs.structured_content == st.unknown("claim", "CLM-010005")


# -------------------------------------------------------------------- payout

NOW = 1_800_000_000
KEY = "run-1:3:0"


def approval(claim: str, amount: int, **changed: Any) -> ApprovalRecord:
    """The agent's record of a grant to pay `claim` `amount`, as the wait writes it."""
    base: dict[str, Any] = {
        "id": "apr_1",
        "action": "issue_payout",
        "args": {"claim_id": claim, "amount": str(amount)},
        "reason": "",
        "customer_id": ROHAN,
        "idempotency_key": KEY,
        "created_at": NOW - 60,
        "expires_at": NOW + 3600,
        "decided": True,
        "granted": True,
        "decided_by": "handler-1",
        "state": ApprovalState.CARRYING_OUT,
    }
    return approval_record(Approval(**{**base, **changed}))


PAYOUTS = [
    # (case, claim, the record, approval named, key sent, paid?, what is said when refused)
    (
        "granted by a handler above the limit",
        "CLM-010004",
        approval("CLM-010004", 25001),
        "apr_1",
        KEY,
        True,
        "",
    ),
    (
        "the limit's own grant, on the limit",
        "CLM-010003",
        approval("CLM-010003", 25000, decided_by=srv.AUTOMATIC_APPROVER),
        "apr_1",
        KEY,
        True,
        "",
    ),
    (
        "carried out already: a retry under its key",
        "CLM-010004",
        approval("CLM-010004", 25001, state=ApprovalState.DONE),
        "apr_1",
        KEY,
        True,
        "",
    ),
    (
        "another amount",
        "CLM-010004",
        approval("CLM-010004", 20000),
        "apr_1",
        KEY,
        False,
        "decided for another amount",
    ),
    (
        "another claim",
        "CLM-010004",
        approval("CLM-010001", 25001),
        "apr_1",
        KEY,
        False,
        "decided for another claim_id",
    ),
    (
        "another person",
        "CLM-010004",
        approval("CLM-010004", 25001, customer_id=MEERA),
        "apr_1",
        KEY,
        False,
        "for another person",
    ),
    (
        "another key",
        "CLM-010004",
        approval("CLM-010004", 25001),
        "apr_1",
        "run-2:1:0",
        False,
        "requested as another call",
    ),
    (
        "expired",
        "CLM-010004",
        approval("CLM-010004", 25001, expires_at=NOW - 1),
        "apr_1",
        KEY,
        False,
        "expired",
    ),
    (
        "above the limit, decided by the automatic limit",
        "CLM-010004",
        approval("CLM-010004", 25001, decided_by=srv.AUTOMATIC_APPROVER),
        "apr_1",
        KEY,
        False,
        "no person decided",
    ),
    (
        "an unknown approval",
        "CLM-010004",
        approval("CLM-010004", 25001),
        "apr_unknown",
        KEY,
        False,
        "needs an approval",
    ),
    ("no approval named", "CLM-010001", None, None, KEY, False, "needs an approval"),
    (
        "refused by the handler",
        "CLM-010004",
        approval("CLM-010004", 25001, granted=False, state=ApprovalState.REFUSED),
        "apr_1",
        KEY,
        False,
        "not granted",
    ),
    (
        "still waiting for a handler",
        "CLM-010004",
        approval("CLM-010004", 25001, decided=False, granted=False, state=ApprovalState.WAITING),
        "apr_1",
        KEY,
        False,
        "not granted",
    ),
    (
        "approved by the policyholder",
        "CLM-010004",
        approval("CLM-010004", 25001, decided_by=ROHAN),
        "apr_1",
        KEY,
        False,
        "nobody but",
    ),
    (
        "granted for another action",
        "CLM-010004",
        approval("CLM-010004", 25001, action="withdraw_claim"),
        "apr_1",
        KEY,
        False,
        "it approved withdraw_claim",
    ),
]


@pytest.mark.discharges("P-PAYOUT", "AHC-0057")
@pytest.mark.parametrize(
    ("case", "claim", "grant", "named", "key", "paid", "why"),
    PAYOUTS,
    ids=[p[0] for p in PAYOUTS],
)
async def test_money_moves_only_on_a_record_that_covers_this_payout(
    case: str,
    claim: str,
    grant: ApprovalRecord | None,
    named: str | None,
    key: str,
    paid: bool,
    why: str,
) -> None:
    """A3: the far end reads the agent's own record through the records port and
    rebuilds the call's digest — this claim, the amount it holds, the
    policyholder it verified, the key on the call — never the wait's engine."""
    held = {grant.id: grant} if grant is not None else {}
    async with claims(held, now=NOW) as (client, _):
        said = await call(client, "issue_payout", {"id": claim}, who=ROHAN, key=key, grant=named)
        after = await call(client, "get_claim", {"id": claim}, who=ROHAN)
    assert (after.structured_content["status"] == "paid") is paid, case
    if paid:
        assert said.structured_content["allowed"] is True
    else:
        assert said.is_error and why in "".join(getattr(b, "text", "") for b in said.content)


async def test_an_account_that_cannot_receive_is_declined_for_good() -> None:
    """`kind: declined` (P-PAYOUT-DECLINED): the same answer however often asked."""
    grant = approval("CLM-010001", 8750, decided_by=srv.AUTOMATIC_APPROVER)
    async with claims({grant.id: grant}, now=NOW) as (client, url):
        async with await psycopg.AsyncConnection.connect(url) as conn:
            await conn.execute(
                "UPDATE policy SET payout_account_open = false WHERE id = 'POL-010001'"
            )
            await conn.commit()
        said = await call(
            client, "issue_payout", {"id": "CLM-010001"}, who=ROHAN, key=KEY, grant="apr_1"
        )
    expected = {"allowed": False, "kind": "declined", "status": "approved"}
    assert subset(said.structured_content, expected) == expected


async def test_a_row_another_writer_holds_is_transient() -> None:
    async with claims() as (client, url):
        async with await psycopg.AsyncConnection.connect(url) as other:
            await other.execute("SELECT 1 FROM claim WHERE id = 'CLM-010005' FOR UPDATE")
            said = await call(client, "withdraw_claim", {"id": "CLM-010005"}, who=ROHAN, key="t1")
            await other.rollback()
        later = await call(client, "withdraw_claim", {"id": "CLM-010005"}, who=ROHAN, key="t1")
    assert subset(said.structured_content, {"allowed": False, "kind": "transient"}) == {
        "allowed": False,
        "kind": "transient",
    }
    assert later.structured_content["allowed"] is True, "a transient refusal is not remembered"


# (case, what the claims system's connection tries, what it gets)
READ_ONLY = [
    ("reads the record the wait wrote", "read", "carrying_out"),
    ("reads nothing for an unknown id", "unknown", None),
    ("cannot write a record", "write", "read-only"),
]


@pytest.mark.discharges("P-PAYOUT", "AHC-0057")
@pytest.mark.parametrize(("case", "move", "expected"), READ_ONLY, ids=[r[0] for r in READ_ONLY])
async def test_the_claims_system_reads_the_agents_records_and_cannot_write_them(
    case: str, move: str, expected: str | None
) -> None:
    """A3 on PostgreSQL: the agent's migrations make the tables, the wait's store
    writes the record, and the claims system reads it through the records port
    on its own connection, opened read-only."""
    grant = approval("CLM-010004", 25001)
    async with throwaway("claims_records") as url:
        await migrate_agent_state(url)
        async with pool(url) as opened:
            await PostgresRecords(opened).put_approval(grant)
        async with approval_records(url) as records:
            if move == "write":
                with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
                    await records.put_approval(grant)  # type: ignore[attr-defined]
                return
            found = await records.approval(grant.id if move == "read" else "apr_unknown")
    assert (found.status if found else None) == expected, case
    if found is not None:
        assert found.args_digest == grant.args_digest, case


# ------------------------------------------------------------ the spec's own


def aoas() -> dict[str, Any]:
    spec: dict[str, Any] = yaml.safe_load(AOAS.read_text())
    return spec


async def test_the_surface_is_the_aoas_claims_systems() -> None:
    operations = aoas()["operations"]
    async with claims() as (client, _):
        tools = (await client.list_tools()).tools
    surface = {
        t.name: (t.meta["side_effect"], t.meta["entity"], t.meta.get("required_scope"))
        for t in tools
    }
    expected = {
        name: (operations[name]["side_effect"], operations[name]["entity"], AGENT_SCOPES.get(name))
        for name in aoas()["external"]["claims_system"]["operations"]
    }
    assert surface == expected
    assert all(t.output_schema for t in tools), "the harness refuses a tool with no outputSchema"


def test_the_far_ends_limit_is_the_aoas() -> None:
    limit = aoas()["operations"]["issue_payout"]["authority"]["agent_when"][0]["at_most"]
    assert limit == srv.AUTOMATIC_LIMIT


async def test_migrations_apply_once() -> None:
    async with throwaway() as url:
        first = await st.migrate(url)
        second = await st.migrate(url)
    assert first == [name for name, _ in st.migration_files()] and second == []


# [name, rows changed after the first seed, a deployed start's seed, the claim's status after]
RESTARTS = [
    ("a replica waking keeps what happened", True, ["--if-empty"], "withdrawn"),
    ("an explicit fresh seed resets the demo", True, ["--fresh"], "registered"),
    ("an untouched database stays as seeded", False, ["--if-empty"], "registered"),
]


@pytest.mark.parametrize(
    ("name", "changed", "flags", "status"), RESTARTS, ids=[r[0] for r in RESTARTS]
)
async def test_a_deployed_start_seeds_only_an_empty_database(
    name: str, changed: bool, flags: list[str], status: str
) -> None:
    from claims_system.__main__ import _seed

    async with throwaway() as url:
        await st.migrate(url)
        assert await st.is_empty(url)
        await _seed(url, fresh=False, if_empty=True)
        assert not await st.is_empty(url)
        async with await psycopg.AsyncConnection.connect(url) as conn:
            claim = (
                await (
                    await conn.execute(
                        "SELECT id FROM claim WHERE status = 'registered' ORDER BY id LIMIT 1"
                    )
                ).fetchone()
            )[0]  # type: ignore[index]
            if changed:
                await conn.execute("UPDATE claim SET status = 'withdrawn' WHERE id = %s", (claim,))
                await conn.commit()
        await _seed(url, fresh="--fresh" in flags, if_empty="--if-empty" in flags)
        async with await psycopg.AsyncConnection.connect(url) as conn:
            now = (
                await (
                    await conn.execute("SELECT status FROM claim WHERE id = %s", (claim,))
                ).fetchone()
            )[0]  # type: ignore[index]
    assert now == status
