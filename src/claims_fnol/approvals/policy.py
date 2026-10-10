"""Which payouts need a claims handler, and for how long a decision stays good.

AOAS `issue_payout.authority`: `agent_when approved_amount at_most 25000`,
otherwise human approval. The number is the AOAS's; this is the one place the
agent enforces it, and `tests/test_payout_limit.py` holds it to the AOAS by
reading it.

**The limit may be lowered while the agent runs** (A6, Tier 5's exercise). It
is read through the harness's config port as `payout.automatic_limit_inr`
(App Configuration on Azure, label `dev`), once per decision, when the payout
is assessed: a change applies to the next payout, never to one half-decided.
The AOAS's number is the key's default and its ceiling: a value above it is
refused and the last good one kept, because a limit above the AOAS's grants
the agent authority the spec does not, and that is a spec change, not a
setting. The claims system reads the same key for its own check (FINDINGS F-71).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal

from agent_harness.approvals.workflow import Terms
from agent_harness.config.settings import Key, Settings, between, defaults

from claims_fnol.binding import SCOPE_PAYOUTS_WRITE

PAYOUT_ACTION = "issue_payout"

AOAS_LIMIT = Decimal("25000")
"""₹25,000: AOAS `issue_payout.authority.agent_when` (approved_amount at_most)."""

AUTOMATIC_LIMIT = Key(
    "payout.automatic_limit_inr", Decimal, AOAS_LIMIT, check=between(1, AOAS_LIMIT)
)
"""The limit as the config port reads it: the AOAS's by default, never above it."""

KEYS = (AUTOMATIC_LIMIT,)
"""Every value this agent reads through the config port (the hook `keys`)."""


@dataclass(frozen=True)
class Policy:
    """What needs a person, and for how long the answer stays good."""

    settings: Settings = field(default_factory=lambda: defaults(*KEYS), compare=False)
    """The config port (A6); without one, the declared defaults."""

    remind_before_s: int = Terms.remind_before_s
    ttl_s: int = Terms.ttl_s
    """P-APPROVAL-TTL `valid_for: 24h`."""

    elevated_scope: str = SCOPE_PAYOUTS_WRITE
    """The scope a grant mints, and nothing else does (AHC-0057)."""

    payable_statuses: frozenset[str] = frozenset({"approved"})
    """AOAS `request_payout.preconditions` and `issue_payout.owed_when`."""

    def automatic_limit(self) -> Decimal:
        """The limit now. Read once per decision, and recorded with it."""
        return self.settings.get(AUTOMATIC_LIMIT)


JUDGED = ("status", "approved_amount")
"""The fields a payout decision rests on (`amount_from: claim.approved_amount`),
and so the fields whose movement makes a grant stale (P-APPROVAL-STALE)."""


def judged(claim: Mapping[str, object]) -> dict[str, str]:
    """The facts a decision about this claim rests on, ready to compare later."""
    return {field: str(claim.get(field)) for field in JUDGED}


def not_requestable(claim: Mapping[str, object], policy: Policy) -> str | None:
    """Why a payout cannot even be requested for this claim, or `None`.

    Says the claim's status and, where it has one, its approved amount — the
    facts the policyholder is told, so a reply can be grounded in them."""
    status = claim.get("status")
    if status in policy.payable_statuses:
        return None
    cid = claim.get("id", "the claim")
    if status == "paid":
        return (
            f"{cid} has already been paid: its approved amount, {claim.get('approved_amount')}, "
            "was paid to the account on the policy, and a claim is paid once"
        )
    return (
        f"{cid} is {status}; a claim is paid once it is approved, and until then its "
        "status is what can be told"
    )


def requires_approval(claim: Mapping[str, object], limit: Decimal) -> str | None:
    """The reason paying this claim needs a claims handler, or `None` when the
    agent may issue it alone — every `agent_when` condition holds.

    The amount is the claim's `approved_amount` as the claims system holds it,
    never a number from the conversation. One that is missing or unreadable
    needs a person: a gate that cannot read the number must not conclude it is
    small. `limit` is the one read for this decision (`Policy.automatic_limit`)."""
    raw = claim.get("approved_amount")
    try:
        amount = Decimal(str(raw))
    except (ArithmeticError, ValueError):
        return f"the claim's approved amount {raw!r} could not be read"
    if raw is None or not amount.is_finite():
        return f"the claim's approved amount {raw!r} could not be read"
    if amount > limit:
        return f"a payout of {amount} is above the {limit} automatic limit"
    return None


__all__ = [
    "AOAS_LIMIT",
    "AUTOMATIC_LIMIT",
    "JUDGED",
    "KEYS",
    "PAYOUT_ACTION",
    "Policy",
    "judged",
    "not_requestable",
    "requires_approval",
]
