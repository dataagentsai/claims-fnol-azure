"""Handing a conversation to a person — this insurer's words and Tier 2 rules.

The waits (the workflow, its Temporal and DBOS realisations, the desk, capacity)
are the harness's (`agent_harness.escalation`), re-exported. What the
policyholder is told (`wording`) and which conditions earn a person (`rules`)
are this agent's. This module *is* the wording the handoff reads.
"""

from __future__ import annotations

from agent_harness.escalation import (
    DEFAULT_TTL_S,
    TASK_QUEUE,
    WORKFLOWS,
    Capacity,
    EscalationDesk,
    EscalationError,
    TemporalEscalations,
    new_escalation_id,
    outcome_of,
    refusal,
    worker,
)

from claims_fnol.escalation.wording import (
    CAPPED_REPLY,
    CLOSED_REPLY,
    LAPSED_REPLY,
    NO_DESK_REPLY,
    QUEUED_REPLY,
    RAISED_REPLY,
    WAITING_REPLY,
    humanise,
)

__all__ = [
    "CAPPED_REPLY",
    "CLOSED_REPLY",
    "DEFAULT_TTL_S",
    "LAPSED_REPLY",
    "NO_DESK_REPLY",
    "QUEUED_REPLY",
    "RAISED_REPLY",
    "TASK_QUEUE",
    "WAITING_REPLY",
    "WORKFLOWS",
    "Capacity",
    "EscalationDesk",
    "EscalationError",
    "TemporalEscalations",
    "humanise",
    "new_escalation_id",
    "outcome_of",
    "refusal",
    "worker",
]
