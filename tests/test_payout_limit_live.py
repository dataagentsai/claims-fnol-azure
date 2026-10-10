"""A6: the automatic payout limit moves without a redeploy, and the money wall holds.

Both sides read `payout.automatic_limit_inr` through their own config port,
once per payout:

- **the agent** when it assesses a payout (`PayoutWork.assess`): a change
  reaches the next payout, and the limit it used is on the decision's span;
- **the claims system** when money would move (`server.covers`): an automatic
  grant above *its* limit is refused.

One source of truth (FINDINGS F-71): both overlays name the same key, label and
store. Wherever the two reads still differ (one cache older than the other),
the lower limit wins — no payout above it moves without a person.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from agent_harness import adapters
from agent_harness import telemetry as tel
from agent_harness.approvals.durable import Ask
from agent_harness.config.settings import Cached, Key
from kit import claims_system, me
from test_claims_system import KEY, NOW, approval
from test_payout_limit import aoas_limit

from claims_fnol.approvals import AUTOMATIC_LIMIT, KEYS, LIMIT_ATTRIBUTE, PayoutWork, Policy
from claims_fnol.approvals.payout import POLICY_APPROVER
from claims_fnol.approvals.policy import requires_approval
from claims_fnol.contracts import Identity
from claims_system import server as srv

ROOT = Path(__file__).resolve().parents[1]


def limits(held: dict[str, object], keys: tuple[Key[Any], ...] = KEYS) -> Cached:
    """A config port over a dict the test changes, re-read at every get."""
    return Cached(keys, lambda names: {n: held[n] for n in names if n in held}, ttl_s=0)


async def acting_for(customer_id: str) -> Identity:
    return me(customer_id)


async def assess(work: PayoutWork, claim_id: str) -> tuple[bool, str]:
    """Assess one payout: (needs a person, the limit its span recorded)."""
    exporter = tel.configure()
    ask = Ask(
        id=f"apr_{claim_id}",
        action="issue_payout",
        args={"claim_id": claim_id},
        customer_id="PH-1001",
        idempotency_key="run_live:1:1",
        ttl_s=86_400,
        queue="approvals",
    )
    assessed = await work.assess(ask)
    (span,) = [s for s in exporter.get_finished_spans() if s.name == "agent.approval.assess"]
    assert tel.validate(exporter.get_finished_spans()) == []
    return assessed.reason is not None, str(span.attributes[LIMIT_ATTRIBUTE])


L = AUTOMATIC_LIMIT.name
# (row, the store before, the store after, the claim paid next, a person decides, limit recorded)
NEXT_PAYOUT: list[tuple[str, dict[str, object], dict[str, object], str, bool, str]] = [
    ("nothing set: the AOAS's limit", {}, {}, "CLM-010003", False, "25000"),
    ("lowered to 20000: 25000 now waits", {}, {L: "20000"}, "CLM-010003", True, "20000"),
    ("lowered to 5000: 8750 now waits", {}, {L: "5000"}, "CLM-010001", True, "5000"),
    ("raised back: 25000 is paid again", {L: "20000"}, {L: "25000"}, "CLM-010003", False, "25000"),
    (
        "above the AOAS's: refused, the last good kept",
        {L: "20000"},
        {L: "30000"},
        "CLM-010003",
        True,
        "20000",
    ),
    ("unreadable: the last good kept", {L: "20000"}, {L: "twenty"}, "CLM-010003", True, "20000"),
    ("removed: the default again", {L: "20000"}, {}, "CLM-010003", False, "25000"),
]


@pytest.mark.discharges("P-PAYOUT", "P-APPROVER", "AHC-0057", "AHC-0003")
@pytest.mark.parametrize(
    ("row", "before", "after", "claim", "person", "recorded"),
    NEXT_PAYOUT,
    ids=[r[0] for r in NEXT_PAYOUT],
)
async def test_a_change_reaches_the_next_payout_and_each_decision_records_its_limit(
    row: str,
    before: dict[str, object],
    after: dict[str, object],
    claim: str,
    person: bool,
    recorded: str,
) -> None:
    held = dict(before)
    async with claims_system() as tools:
        work = PayoutWork(tools, acting_for=acting_for, policy=Policy(settings=limits(held)))
        first = await assess(work, "CLM-010001")
        held.clear()
        held.update(after)  # the store changes; nothing is rebuilt
        assert await assess(work, claim) == (person, recorded), row
    assert first == (False, str(before.get(L, "25000")))


# (row, the agent's limit, the claims system's limit, the approved amount)
LIMITS: list[tuple[str, int, int, int]] = [
    ("the same limit, on it", 20000, 20000, 20000),
    ("the same limit, past it", 20000, 20000, 20001),
    ("the wall lower: within it", 25000, 20000, 19000),
    ("the wall lower: between the two", 25000, 20000, 22000),
    ("the agent lower: within it", 20000, 25000, 19000),
    ("the agent lower: between the two", 20000, 25000, 22000),
    ("both: above either", 20000, 25000, 25001),
]


@pytest.mark.discharges("P-PAYOUT", "AHC-0057")
@pytest.mark.parametrize(("row", "agent", "wall", "amount"), LIMITS, ids=[r[0] for r in LIMITS])
def test_where_the_two_limits_differ_the_stricter_wins(
    row: str, agent: int, wall: int, amount: int
) -> None:
    """Money moves without a person only at or below both limits: the agent
    sends a payout above its own to a person, and the claims system refuses an
    automatic grant above its own."""
    claim = {"id": "CLM-010004", "status": "approved", "approved_amount": amount}
    agent_grants = requires_approval(claim, Policy(settings=limits({L: agent})).automatic_limit())
    wall_settings = limits({L: wall}, srv.KEYS)
    record = approval("CLM-010004", amount, decided_by=srv.AUTOMATIC_APPROVER)
    wall_says = srv.covers(
        record,
        holder="PH-1001",
        claim=claim,
        key=KEY,
        now=NOW,
        limit=wall_settings.get(srv.AUTOMATIC_LIMIT),
    )
    moved_without_a_person = agent_grants is None and wall_says is None
    assert moved_without_a_person is (amount <= min(agent, wall)), row
    if agent_grants is None and amount > wall:
        assert wall_says is not None and f"above the {wall} automatic limit" in wall_says


@pytest.mark.discharges("P-PAYOUT", "AHC-0057")
def test_both_sides_declare_one_key_with_the_aoas_as_default_and_ceiling() -> None:
    assert srv.AUTOMATIC_LIMIT.name == AUTOMATIC_LIMIT.name == "payout.automatic_limit_inr"
    assert srv.AUTOMATIC_LIMIT.default == AUTOMATIC_LIMIT.default == aoas_limit()
    assert POLICY_APPROVER == srv.AUTOMATIC_APPROVER
    for key in (srv.AUTOMATIC_LIMIT, AUTOMATIC_LIMIT):
        with pytest.raises(ValueError, match="refused"):
            key.parse(str(aoas_limit() + 1))


# (environment, the agent's overlay, the claims system's) — the same source each time
OVERLAYS = [
    ("local", "config/local.yaml", "config/claims-system/local.yaml"),
    ("test", "config/test.yaml", "config/claims-system/test.yaml"),
    ("azure", "config/azure.yaml", "config/claims-system/azure.yaml"),
]


@pytest.mark.discharges("AHC-0022")
@pytest.mark.parametrize(("env", "agent", "wall"), OVERLAYS, ids=[o[0] for o in OVERLAYS])
def test_the_agent_and_the_claims_system_read_one_source(env: str, agent: str, wall: str) -> None:
    def config(path: str) -> dict[str, Any]:
        entry = dict(yaml.safe_load((ROOT / path).read_text())["bindings"]["config"])
        entry.pop("why", None)
        dotenv = entry.pop("dotenv", None)
        if dotenv is not None:
            entry["dotenv"] = (ROOT / path).parent.joinpath(dotenv).resolve()
        return entry

    assert config(agent) == config(wall)
    assert (
        adapters.plan(ROOT / agent).adapter("config")
        == {
            "local": "environment-settings",
            "test": "static",
            "azure": "app-configuration",
        }[env]
    )
    if env == "azure":
        assert config(agent)["label"] == "dev"
        assert config(agent)["endpoint"] == {"env": "AZURE_APP_CONFIGURATION_ENDPOINT"}


@pytest.mark.discharges("AHC-0057")
def test_the_claims_system_without_a_port_enforces_the_aoas_limit() -> None:
    claim = {"id": "CLM-010004", "status": "approved", "approved_amount": 25001}
    record = approval("CLM-010004", 25001, decided_by=srv.AUTOMATIC_APPROVER)
    said = srv.covers(record, holder="PH-1001", claim=claim, key=KEY, now=NOW)
    assert said is not None and "above the 25000 automatic limit" in said
