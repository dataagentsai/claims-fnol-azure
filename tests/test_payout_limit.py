"""The automatic payout limit is the AOAS's, and the boundary sits exactly where it says.

AOAS `issue_payout.authority.agent_when: approved_amount at_most 25000`. The
policy's limit is read against the AOAS here, never copied into a second place
nobody checks (the clothing agent had three copies, F-088); and the boundary is
held by a table: on the limit the agent pays alone, one rupee past it a claims
handler decides.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
import yaml
from kit import AOAS

from claims_fnol.approvals.policy import (
    JUDGED,
    Policy,
    judged,
    not_requestable,
    requires_approval,
)

SPEC = yaml.safe_load(AOAS.read_text())


def aoas_limit() -> Decimal:
    bounds = [
        c["at_most"]
        for c in SPEC["operations"]["issue_payout"]["authority"]["agent_when"]
        if c.get("field") == "approved_amount" and "at_most" in c
    ]
    assert len(bounds) == 1, "the AOAS states one limit on the approved amount"
    return Decimal(bounds[0])


@pytest.mark.discharges("P-PAYOUT", "P-APPROVER")
def test_the_enforced_limit_is_the_aoas_limit() -> None:
    assert Policy().automatic_limit == aoas_limit() == Decimal("25000")


# [approved amount, needs a claims handler]
AMOUNTS = [
    (1, False),
    (8750, False),
    (24999, False),
    (25000, False),  # exactly on the limit: at_most
    (25001, True),  # one rupee past it
    (100000, True),
    ("not a number", True),  # unreadable: a gate that cannot read the number never calls it small
    (None, True),
]


@pytest.mark.discharges("P-PAYOUT", "P-APPROVER", "op:issue_payout", "AHC-0057")
@pytest.mark.parametrize(("amount", "needs_a_person"), AMOUNTS, ids=[str(a[0]) for a in AMOUNTS])
def test_the_limit_decides_who_authorises(amount: object, needs_a_person: bool) -> None:
    claim = {"id": "CLM-010001", "status": "approved", "approved_amount": amount}
    assert (requires_approval(claim, Policy()) is not None) is needs_a_person


# [claim status, may a payout be requested]
STATUSES = [
    ("approved", True),
    ("paid", False),
    ("registered", False),
    ("documents_pending", False),
    ("under_assessment", False),
    ("rejected", False),
    ("withdrawn", False),
]


@pytest.mark.discharges("P-PAYOUT-OWED", "op:request_payout")
@pytest.mark.parametrize(("status", "payable"), STATUSES, ids=[s[0] for s in STATUSES])
def test_only_an_approved_claim_can_be_paid(status: str, payable: bool) -> None:
    claim = {"id": "CLM-010002", "status": status, "approved_amount": 6800}
    refused = not_requestable(claim, Policy())
    assert (refused is None) is payable
    if status == "paid":
        assert refused is not None and "already been paid" in refused and "6800" in refused


@pytest.mark.discharges("P-APPROVAL-STALE", "AHC-0057")
def test_a_decision_rests_on_status_and_approved_amount() -> None:
    assert set(JUDGED) == {"status", "approved_amount"}
    assert SPEC["operations"]["issue_payout"]["amount_from"] == "claim.approved_amount"
    assert judged({"status": "approved", "approved_amount": 25001, "note": "x"}) == {
        "status": "approved",
        "approved_amount": "25001",
    }


@pytest.mark.discharges("P-APPROVAL-TTL")
def test_a_grant_is_good_for_the_aoas_validity() -> None:
    statement = next(
        s for s in SPEC["policies"]["approval"]["statements"] if s["id"] == "P-APPROVAL-TTL"
    )
    assert statement["valid_for"] == "24h"
    assert Policy().ttl_s == 24 * 60 * 60
