"""F-117: a policyholder comes back to their conversation.

The page held the conversation id in memory only, so a reload or a closed tab
started again and a follow-up lost its context. `/opening` now returns the
newest conversation to continue, found by the verified customer id.
"""

from __future__ import annotations

from typing import Any

from agent_harness.contracts import Message
from agent_harness.state import Conversation
from test_app import app

from claims_fnol_app import resume

NOW = 1_800_000_000
HOUR = 3600


def stored(customer: str = "PH-1001", **pending: str) -> bytes:
    conversation = Conversation(
        conversation_id="cnv_test",
        customer_id=customer,
        messages=(
            Message(role="user", content="a bus hit my car"),
            Message(role="assistant", content="which policy?"),
        ),
        **pending,
    )
    return conversation.encode()


# (why, found, hours, resumed?)
RESUMES = [
    ("nothing stored: a fresh conversation", None, 24, False),
    ("written an hour ago: continued", (stored(), NOW - HOUR), 24, True),
    ("written exactly at the limit: continued", (stored(), NOW - 24 * HOUR), 24, True),
    ("written a day and a second ago: fresh", (stored(), NOW - 24 * HOUR - 1), 24, False),
    (
        "a week old but a payout waits: continued",
        (stored(pending_approval_id="apr_1"), NOW - 7 * 24 * HOUR),
        24,
        True,
    ),
    (
        "a week old but a person holds it: continued",
        (stored(pending_escalation_id="E-1"), NOW - 7 * 24 * HOUR),
        24,
        True,
    ),
    ("hours = 0 and nothing waits: fresh", (stored(), NOW - 60), 0, False),
    (
        "hours = 0 but a payout waits: continued",
        (stored(pending_approval_id="apr_1"), NOW - 60),
        0,
        True,
    ),
    ("someone else's conversation: never", (stored(customer="PH-1002"), NOW - 60), 24, False),
]


def test_resumable() -> None:
    for why, found, hours, expected in RESUMES:
        got = resume.resumable(found, customer_id="PH-1001", now=NOW, within_hours=hours)
        assert (got is not None) is expected, why


# (why, messages, shown)
EARLIER = [
    ("none: nothing shown", (), []),
    (
        "user and assistant only, in order",
        (
            Message(role="user", content="a"),
            Message(role="tool", content="t", tool_call_id="x"),
            Message(role="assistant", content="b"),
        ),
        [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}],
    ),
    (
        "only the newest EARLIER_SHOWN",
        tuple(Message(role="user", content=str(i)) for i in range(resume.EARLIER_SHOWN + 5)),
        [{"role": "user", "content": str(i)} for i in range(5, resume.EARLIER_SHOWN + 5)],
    ),
]


def test_earlier() -> None:
    for why, messages, shown in EARLIER:
        conversation = Conversation(
            conversation_id="cnv_t", customer_id="PH-1001", messages=messages
        )
        assert resume.earlier(conversation) == shown, why
    assert resume.earlier(None) == []


async def test_the_opening_continues_the_policyholders_own_conversation() -> None:
    """Rohan asks, reloads (same conversation, earlier messages back), asks for a
    new one (none); Meera never gets Rohan's."""
    async with app([]) as a:
        said = await a.say("rohan", "What's the status of claim CLM-010007?")
        started = said.json()["conversation_id"]

        async def opening(login: str, query: str = "") -> Any:
            auth = {"authorization": f"Bearer {a.token(login)}"}
            return (await a.http.get(f"/opening{query}", headers=auth)).json()

        reloaded = await opening("rohan")
        fresh = await opening("rohan", "?new=1")
        stranger = await opening("meera")

    assert reloaded["conversation_id"] == started
    assert [m["role"] for m in reloaded["earlier"]] == ["user", "assistant"]
    assert reloaded["earlier"][0]["content"] == "What's the status of claim CLM-010007?"
    assert "CLM-010007" in reloaded["reply"]  # the greeting is still there
    assert fresh["conversation_id"] is None and fresh["earlier"] == []
    assert stranger["conversation_id"] is None and stranger["earlier"] == []
