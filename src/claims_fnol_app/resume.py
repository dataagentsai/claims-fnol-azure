"""Which conversation a policyholder comes back to (F-117).

The chat page held the conversation id in memory only, so a reload, a closed
tab or a second device started a new conversation and a follow-up ("pol 1")
lost its context. Now `/opening` looks up the policyholder's newest
conversation — by the customer id their verified session carries, never one the
browser sends — and resumes it when it is recent enough, or when something in it
is still waiting for a person (a payout, a hand-off), however old.

How recent is one App Configuration key, `conversation.resume_hours` (label
dev), changed without a redeploy like the payout limit; 0 resumes only waiting
conversations.
"""

from __future__ import annotations

from agent_harness.config.settings import Key, between
from agent_harness.state import Conversation

RESUME_HOURS = Key("conversation.resume_hours", int, 24, check=between(0, 24 * 30))
"""Resume a conversation last written within this many hours (0 to 720)."""

EARLIER_SHOWN = 20
"""How many earlier messages the page is given back: enough to read the thread,
not the whole of a long day (the stored history is bounded at 32,000 chars)."""


def resumable(
    found: tuple[bytes, int] | None, *, customer_id: str, now: int, within_hours: int
) -> Conversation | None:
    """The conversation to continue, or `None` for a fresh one."""
    if found is None:
        return None
    state, written_at = found
    conversation = Conversation.decode(state)
    if conversation.customer_id != customer_id:
        return None  # never someone else's, whatever the store returned
    if conversation.pending_approval_id or conversation.pending_escalation_id:
        return conversation  # still waiting for a person: always come back to it
    if within_hours <= 0:
        return None
    return conversation if now - written_at <= within_hours * 3600 else None


def earlier(conversation: Conversation | None) -> list[dict[str, str]]:
    """What the page shows above the greeting: the policyholder's and the
    agent's words only, the newest `EARLIER_SHOWN`."""
    if conversation is None:
        return []
    said = [m for m in conversation.messages if m.role in ("user", "assistant")]
    return [{"role": m.role, "content": m.content} for m in said[-EARLIER_SHOWN:]]


__all__ = ["EARLIER_SHOWN", "RESUME_HOURS", "earlier", "resumable"]
