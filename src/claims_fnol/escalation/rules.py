"""Tier 2 — the escalations nobody asked for, as the AOAS's `escalation.on_condition` lists them.

The engine (conditions, cooldown, cap, the facts a rule reads, `evaluate`) is the
harness's (`agent_harness.escalation.rules`). The rules — which conditions earn
a policyholder a person, in what order, how long each waits — are the AOAS's,
transcribed here and held to it by a table test.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from agent_harness.escalation import rules as _engine
from agent_harness.escalation.rules import (
    MAX_PER_CONVERSATION,
    TERMINATED_BADLY,
    Condition,
    Facts,
    Tier2Rule,
    facts_of,
)

DEFAULT_RULES: tuple[Tier2Rule, ...] = (
    Tier2Rule(
        id="declined",
        when=(Condition(field="termination", equals=("declined",)),),
        reason="a payout the policyholder's account can no longer receive",
        priority=1,
        ttl_s=30 * 60,
    ),
    Tier2Rule(
        id="loop-exhausted",
        when=(Condition(field="termination", equals=TERMINATED_BADLY),),
        reason="the agent could not complete this and stopped",
        priority=2,
        ttl_s=15 * 60,
    ),
    Tier2Rule(
        id="tool-unavailable",
        when=(Condition(field="consecutive_failed", at_least=2),),
        reason="the claims system is not responding",
        priority=2,
        ttl_s=15 * 60,
    ),
    Tier2Rule(
        id="repeated-intent",
        when=(Condition(field="repeated_intent", at_least=3),),
        reason="the policyholder has asked for the same thing three times without resolution",
        priority=3,
        ttl_s=30 * 60,
    ),
    Tier2Rule(
        id="second-refusal",
        when=(Condition(field="refusals", at_least=2),),
        reason="the agent has refused this policyholder twice and a person should decide",
        priority=4,
        ttl_s=30 * 60,
    ),
    Tier2Rule(
        id="turns-exceeded",
        when=(Condition(field="turn_count", at_least=10),),
        reason="this conversation has run long without resolving",
        priority=6,
        ttl_s=45 * 60,
    ),
)


@dataclass(frozen=True)
class RuleSet(_engine.RuleSet):
    """Versioned configuration, like `router.Rules`."""

    rules: tuple[Tier2Rule, ...] = field(default_factory=lambda: DEFAULT_RULES)


def evaluate(facts: Facts, rules: _engine.RuleSet | None = None) -> Tier2Rule | None:
    """The first rule that holds, or nothing — this insurer's rules unless told otherwise."""
    return _engine.evaluate(facts, rules or RuleSet())


__all__ = [
    "DEFAULT_RULES",
    "MAX_PER_CONVERSATION",
    "TERMINATED_BADLY",
    "Condition",
    "Facts",
    "RuleSet",
    "Tier2Rule",
    "evaluate",
    "facts_of",
]
