"""This agent's own spans, declared into the harness's span contract, and its names.

Six spans are opened by this agent's modules rather than the harness's — the
router, the deterministic answers, the opening, the payout's resume and carry-out,
and the promise gate — so they are this agent's to declare.
"""

from __future__ import annotations

from agent_harness import telemetry
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
    "agent.approval.resume": SpanSpec(required=frozenset({"agent.approval.id"})),
    "agent.approval.carry_out": SpanSpec(required=frozenset({"agent.approval.id"})),
    "agent.promise.unbacked": SpanSpec(
        required=frozenset({"agent.promise.kind", "agent.promise.rules_version"})
    ),
}

SCOPE = "claims_fnol"
SERVICE = "claims-fnol"

telemetry.identify(scope=SCOPE, service=SERVICE)
declare(SPANS)

__all__ = ["SCOPE", "SERVICE", "SPANS"]
