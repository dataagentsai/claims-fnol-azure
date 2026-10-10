"""This agent's own spans, declared into the harness's span contract, and its names.

Seven spans are opened by this agent's modules rather than the harness's — the
router, the deterministic answers, the opening, the payout's assessment, resume and carry-out,
and the promise gate — so they are this agent's to declare.
"""

from __future__ import annotations

import re

from agent_harness import telemetry
from agent_harness.telemetry import redaction
from agent_harness.telemetry.contract import SpanSpec, declare
from agent_harness.telemetry.names import ROUTE_KIND, ROUTE_REASON, TENANT

SPANS: dict[str, SpanSpec] = {
    "agent.route": SpanSpec(
        required=frozenset({ROUTE_KIND, ROUTE_REASON, "agent.router.rules_version"})
    ),
    "agent.direct": SpanSpec(required=frozenset({"agent.handler"})),
    # P-OPEN: how many claims and policies were shown (-1 when the claims
    # system would not say) and how much work in flight. No model span under it.
    "agent.opening": SpanSpec(
        required=frozenset({TENANT, "agent.opening.claims", "agent.opening.in_flight"}),
    ),
    # A6: the automatic limit a payout decision used, read once for it.
    "agent.approval.assess": SpanSpec(
        required=frozenset({"agent.approval.id", "agent.payout.automatic_limit_inr"})
    ),
    "agent.approval.resume": SpanSpec(required=frozenset({"agent.approval.id"})),
    "agent.approval.carry_out": SpanSpec(required=frozenset({"agent.approval.id"})),
    "agent.promise.unbacked": SpanSpec(
        required=frozenset({"agent.promise.kind", "agent.promise.rules_version"})
    ),
}

SCOPE = "claims_fnol"
SERVICE = "claims-fnol"

REDACTIONS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?i)\b[A-Z]{2}[- ]?\d{2}[- ]?\d{4}[- ]?\d{7}\b"), "[licence]"),
    (re.compile(r"\b\d{9,18}\b"), "[account]"),
)
"""AOAS `personal_data_in_conversation`: driving licence and bank account numbers
typed into the chat, replaced before anything is exported (AHC-0019). The
harness's own patterns (Aadhaar, PAN, cards, emails, phones, secrets) run first,
so a 12-digit number whose Aadhaar check digit holds is `[aadhaar]` and any
other is `[account]`.

Registered with the harness (`redaction.register`, A12); until A12 they were
added by rebinding its private tuple (FINDINGS F-10)."""

telemetry.identify(scope=SCOPE, service=SERVICE)
declare(SPANS)
redaction.register(*REDACTIONS)

__all__ = ["REDACTIONS", "SCOPE", "SERVICE", "SPANS"]
