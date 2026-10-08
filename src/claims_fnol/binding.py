"""What this agent tells the model it is, and the names its credentials carry.

Which operations are privileged is the AOAS's (`issue_payout.authority`); what
each privilege is *called* belongs to whatever issues credentials. The map below
is the same statement as `bindings.tool_runtime.x_scopes` in
`harness-profile.yaml`, and a test holds the two equal.
"""

from __future__ import annotations

SYSTEM_PROMPT = (
    "You are the claims assistant of a motor insurer, talking to a signed-in "
    "policyholder about their own policies and claims. Answer only from what the "
    "tools return. Never say or imply that a claim is covered, will be approved or "
    "paid, or for what amount — only the assessment decides that. Never promise a "
    "date. If you cannot do something, say so plainly and say what you can do. "
    "Register a new claim only on an active policy the policyholder holds, and give "
    "them its reference and that an assessor will look at it next. Asked about a "
    "claim or policy without its reference, list theirs instead of asking."
)
"""Prompt v1. This insurer's words; the composition root only passes them on."""

SCOPE_CLAIMS_READ = "claims:read"
SCOPE_CLAIMS_WRITE = "claims:write"
SCOPE_PAYOUTS_WRITE = "payouts:write"

POLICYHOLDER_SCOPES = frozenset({SCOPE_CLAIMS_READ, SCOPE_CLAIMS_WRITE})
"""What a policyholder's session holds. `payouts:write` is deliberately absent:
the payout is issued only under a grant (AHC-0057), by the approvals worker."""

SCOPES: dict[str, str] = {
    "register_claim": SCOPE_CLAIMS_WRITE,
    "submit_document": SCOPE_CLAIMS_WRITE,
    "withdraw_claim": SCOPE_CLAIMS_WRITE,
    "issue_payout": SCOPE_PAYOUTS_WRITE,
}
"""Operation to the scope a caller must hold. A read needs none: the claims
system answers about the caller's own rows and nothing else (P-OWNERSHIP)."""

SESSION_FIELD = "policyholder_id"
"""The AOAS `session` block's field, which ownership compares against."""

FRESH_FOR_S = 30
"""AOAS `claim.status` `fresh_for: 30s`: the number is the specification's,
acting on it is the harness's (AHC-0107, AHC-0117)."""

__all__ = [
    "FRESH_FOR_S",
    "POLICYHOLDER_SCOPES",
    "SCOPES",
    "SCOPE_CLAIMS_READ",
    "SCOPE_CLAIMS_WRITE",
    "SCOPE_PAYOUTS_WRITE",
    "SESSION_FIELD",
    "SYSTEM_PROMPT",
]
