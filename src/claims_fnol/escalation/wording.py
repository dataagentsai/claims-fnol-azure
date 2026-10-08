"""What the policyholder is told about the desk — only what is now true (P-ESC-TOLD).

Every sentence the harness's handoff says, in this insurer's voice: the handoff
reads them from `claims_fnol.escalation` at the moment it speaks.
"""

from __future__ import annotations

RAISED_REPLY = (
    "I have passed this to a colleague in our claims team. Your reference is {ticket}, "
    "and they will pick this up here."
)
"""A reference and no time: nothing here knows when a person will arrive."""

WAITING_REPLY = (
    "This is with a colleague in our claims team — your reference is {ticket}. "
    "They will answer you here."
)

QUEUED_REPLY = (
    "I have passed this to a colleague in our claims team — your reference is {ticket}, "
    "and the wait is about {wait}."
)
"""A wait only when one is measured from queue depth and throughput (P-ESC-TOLD)."""

CLOSED_REPLY = (
    "Our claims team is not available right now. I have logged this as {ticket} and "
    "they will pick it up when they are back."
)

NO_DESK_REPLY = (
    "I cannot pass this to a colleague from here. Tell me what you need and I will do what I can."
)
"""AOAS `escalate.on_refusal`: say the handover cannot be made and stay with the request."""

CAPPED_REPLY = (
    "This is already with a colleague, and raising it again would not move it any faster. "
    "Tell me what you need in the meantime and I will do what I can."
)
"""Past P-ESC-CAP: something true instead of another reference."""

LAPSED_REPLY = (
    "Nobody has picked up {ticket} yet, so I am back with you in the meantime. "
    "Tell me what you need and I will do what I can."
)
"""P-ESC-LAPSE: the conversation returns, and the policyholder is told nobody came."""


def humanise(seconds: int) -> str:
    """A wait a person can act on, rounded: false precision reads as a guarantee."""
    if seconds < 90:
        return "a minute"
    minutes = round(seconds / 60)
    if minutes < 60:
        return f"{minutes} minutes"
    hours = round(minutes / 60)
    return "an hour" if hours == 1 else f"{hours} hours"


__all__ = [
    "CAPPED_REPLY",
    "CLOSED_REPLY",
    "LAPSED_REPLY",
    "NO_DESK_REPLY",
    "QUEUED_REPLY",
    "RAISED_REPLY",
    "WAITING_REPLY",
    "humanise",
]
