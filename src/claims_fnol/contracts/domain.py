"""This insurer's vocabulary: what a policyholder can ask, and a claim's and a policy's states.

L6. Every name here is the AOAS's (`intents`, `state_machines.claim_status`,
`entities.policy.status`), spelled as the AOAS spells it, so a test can hold
the two equal by reading the spec rather than a copy of it.
"""

from __future__ import annotations

from enum import StrEnum


class Intent(StrEnum):
    """What the policyholder is asking for. Classified deterministically, per turn."""

    CLAIM_STATUS = "claim_status"
    POLICY_STATUS = "policy_status"
    PAYOUT_STATUS = "payout_status"
    REPORT_CLAIM = "report_claim"
    SEND_DOCUMENT = "send_document"
    WITHDRAW_CLAIM = "withdraw_claim"
    PAYOUT_REQUEST = "payout_request"
    HUMAN = "human"
    INJURY = "injury"
    COVERAGE_QUESTION = "coverage_question"
    LIABILITY_QUESTION = "liability_question"
    POLICY_CHANGE = "policy_change"
    ROADSIDE_ASSISTANCE = "roadside_assistance"
    OTHER = "other"


class ClaimStatus(StrEnum):
    """AOAS `state_machines.claim_status`.

    registered → documents_pending → under_assessment → approved → paid
         ↓               ↓                    ↓
     withdrawn       withdrawn             rejected
    """

    REGISTERED = "registered"
    DOCUMENTS_PENDING = "documents_pending"
    UNDER_ASSESSMENT = "under_assessment"
    APPROVED = "approved"
    REJECTED = "rejected"
    PAID = "paid"
    WITHDRAWN = "withdrawn"


class PolicyStatus(StrEnum):
    ACTIVE = "active"
    LAPSED = "lapsed"
    CANCELLED = "cancelled"


__all__ = ["ClaimStatus", "Intent", "PolicyStatus"]
