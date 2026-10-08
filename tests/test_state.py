"""What a turn leaves behind: a checkpoint, a structured record beside the transcript, one run per delivery, and nothing kept past 30 days.

The stores and the record are the harness's (`agent_harness.state`,
`agent_harness.requests`, `agent_harness.erasure`); the AOAS's retention and the
facts it lists are this agent's, held to them here.
"""

from __future__ import annotations

import time

import pytest
import yaml
from agent_harness.contracts import AlreadyAnswered
from agent_harness.erasure import forget
from agent_harness.erasure.retention import RETENTION_DAYS, expire
from agent_harness.requests import InMemoryRequests
from agent_harness.state import Conversation, InMemoryCheckpointStore
from kit import AOAS, ROHAN, agent, me, says

from claims_fnol import entrypoint as ep
from claims_fnol.config import Settings

SPEC = yaml.safe_load(AOAS.read_text())


@pytest.mark.discharges("AHC-0044", "AHC-0102", "AHC-0010")
async def test_each_turn_is_checkpointed_whole_and_reads_back_as_written() -> None:
    async with agent([says("Hello — I can help with your claims.")]) as (built, _):
        _, conversation = await built.handle("hello", identity=me())
        stored = await built.store.latest(conversation.conversation_id)
    assert stored is not None
    assert Conversation.decode(stored) == conversation
    with pytest.raises(ValueError):  # a record that is not what was written is refused, never guessed at
        Conversation.decode(stored[: len(stored) // 2])


@pytest.mark.discharges("AHC-0102")
def test_stores_that_disagree_about_surviving_a_restart_are_refused() -> None:
    class Durable:
        durable = True

    with pytest.raises(ValueError, match="durable"):
        ep.build(llm=None, tools=None, store=InMemoryCheckpointStore(), approvals=Durable())  # type: ignore[arg-type]


# [words, model answers, what the record holds afterwards]
RECORDS = [
    ("Where is my claim CLM-010006?", [], {"read": "get_claim:CLM-010006"}),
    (
        "Please withdraw claim CLM-010005, I'll pay myself",
        [says("", ("withdraw_claim", {"id": "CLM-010005"})), says("Claim CLM-010005 has been withdrawn.")],
        {"done": "withdraw_claim:CLM-010005"},
    ),
    ("Where is my claim CLM-010007? Also, can you send a tow truck?", [], {"concerns": 2}),
]


@pytest.mark.discharges("AHC-0108", "AHC-0118", "P-CONCERNS")
@pytest.mark.parametrize(("words", "answers", "holds"), RECORDS, ids=["a read", "an effect", "two concerns"])
async def test_a_structured_record_of_the_work_runs_beside_the_transcript(
    words: str, answers: list[object], holds: dict[str, object]
) -> None:
    from evals.durable import escalations_for

    async with escalations_for() as waits, agent(answers, escalations=waits.escalations) as (built, _):
        _, conversation = await built.handle(words, identity=me())
    facts = conversation.facts
    assert facts.asked == words
    if "read" in holds:
        assert holds["read"] in facts.read
    if "done" in holds:
        assert any(holds["done"] in d for d in facts.done), facts.done
    if "concerns" in holds:
        assert len(facts.concerns) == holds["concerns"]
        handed = facts.as_handoff()
        assert "CLM-010007" in handed and "tow" in handed


@pytest.mark.discharges("AHC-0053")
async def test_one_delivery_produces_exactly_one_run() -> None:
    """A redelivered message runs nothing and is handed what the first run
    answered; another policyholder presenting the same key runs their own."""
    answers = [says("Hello — I can help with your claims."), says("Hello, Meera.")]
    async with agent(answers, deliveries=InMemoryRequests()) as (built, llm):
        first, _ = await built.handle("hello", identity=me(), delivery_id="msg-1")
        with pytest.raises(AlreadyAnswered) as again:
            await built.handle("hello", identity=me(), delivery_id="msg-1")
        other, _ = await built.handle("hello", identity=me("PH-1002"), delivery_id="msg-1")
    assert len(llm.calls) == 2
    assert again.value.outcome is not None
    assert getattr(first, "reply", "") in str(again.value.outcome)
    assert getattr(other, "reply", "") == "Hello, Meera."


def days(prop: str) -> int:
    must = next(p["must"] for p in SPEC["required"]["properties"] if p["id"] == prop)
    return int(must.split(" days")[0].split()[-1])


@pytest.mark.discharges("Q-RETENTION", "AHC-0115")
def test_every_copy_of_the_retention_window_is_the_aoas() -> None:
    assert days("Q-RETENTION") == Settings().retention_days == RETENTION_DAYS == 30


@pytest.mark.discharges("Q-RETENTION")
async def test_records_older_than_the_window_are_deleted() -> None:
    async with agent([says("Hello.")]) as (built, _):
        _, conversation = await built.handle("hello", identity=me())
        store = built.store
        kept = await expire(now=int(time.time()) + 29 * 86400, checkpoints=store)
        assert kept.runs == 0 and await store.latest(conversation.conversation_id) is not None
        gone = await expire(now=int(time.time()) + 31 * 86400, checkpoints=store)
        assert gone.runs >= 1 and await store.latest(conversation.conversation_id) is None


@pytest.mark.discharges("AHC-0115")
async def test_one_policyholder_can_be_forgotten_and_the_report_says_what_went() -> None:
    async with agent([says("Hello."), says("Hello.")]) as (built, _):
        _, mine = await built.handle("hello", identity=me(ROHAN))
        _, theirs = await built.handle("hello", identity=me("PH-1002"))
        erased = await forget(customer_id=ROHAN, checkpoints=built.store)
        assert erased.runs >= 1 and erased.anything
        assert await built.store.latest(mine.conversation_id) is None
        assert await built.store.latest(theirs.conversation_id) is not None
