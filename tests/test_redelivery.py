"""A crash mid-turn must not repeat a side effect (Tier 4a, A5).

The case: a turn calls `register_claim`, the process dies before the turn's
checkpoint (and before its delivery claim is settled, because `finally` does not
run for a killed process), the claim expires, and the message is delivered
again. The retry runs the same turn. Before A5 it ran under a fresh run id, so
`register_claim` went out under a new idempotency key and the far end made a
second claim. Now the run is named by the delivery
(`agent_harness.entrypoint.persist.delivered`), the keys repeat, and the far end
answers the repeat with its first answer.

The far end is the AgentTwin world, which ignores `register_claim.identity`
(FINDINGS F-22): only the key can stop a second claim there, so the table shows
the key doing it. Our own claims system would also catch the repeat by identity.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

import pytest
from agent_harness.contracts import CLAIM_TTL_S, Claim, Scope
from agent_harness.entrypoint.persist import delivered
from agent_harness.llm import ScriptedClient
from agent_harness.requests import InMemoryRequests
from agent_harness.state import Conversation, InMemoryCheckpointStore
from agent_harness.tools import connect
from agenttwin import project
from evals.simulation import Entities, as_policyholder, entities_of
from kit import ROHAN, live, me, says

from claims_fnol import entrypoint as ep
from claims_fnol.binding import SCOPES
from claims_fnol.contracts import (
    ConversationId,
    IdempotencyKey,
    Identity,
    RunId,
    ToolClient,
    ToolRegistry,
    ToolResult,
)

REPORT = "A bus hit my car KA-01-AB-1234 this morning. I want to make a claim."


def script() -> list[object]:
    return [
        says("", ("register_claim", {"id": "POL-010001", "incident_type": "collision"})),
        says("I've registered the collision on your policy."),
    ]


class Crash(BaseException):
    """The process dying: not an `Exception`, so nothing on the way out handles it."""


@dataclass
class Dies:
    """The checkpoint store of a process killed just before it writes."""

    durable = False

    async def checkpoint(self, *args: object, **kwargs: object) -> None:
        raise Crash("killed before the checkpoint")

    async def latest(self, conversation_id: ConversationId) -> bytes | None:
        return None


@dataclass
class Gone:
    """The delivery ledger as a killed process leaves it: the claim was taken,
    and nothing settles or releases it — it lapses at its expiry."""

    inner: InMemoryRequests
    durable = False

    async def claim(self, name: str, *, scope: Scope, ttl_s: int = CLAIM_TTL_S) -> Claim:
        return await self.inner.claim(name, scope=scope, ttl_s=ttl_s)

    async def settle(self, name: str, outcome: dict[str, object] | None = None) -> None:
        return None

    async def abandon(self, name: str) -> None:
        return None


@dataclass
class Keys:
    """The tool client, noting the key each `register_claim` went out under."""

    inner: ToolClient
    sent: list[tuple[str, str]] = field(default_factory=list)

    async def list_tools(self, identity: Identity) -> ToolRegistry:
        return await self.inner.list_tools(identity)

    async def call(
        self, name: str, arguments: dict[str, object], identity: Identity, key: IdempotencyKey
    ) -> ToolResult:
        result = await self.inner.call(name, arguments, identity, key)
        if name == "register_claim":
            created = (result.structured or {}).get("created", "")
            self.sent.append((key.value, str(created)))
        return result


@dataclass
class Monotonic:
    at: float = 1_000.0

    def __call__(self) -> float:
        return self.at


Again = Callable[[], tuple[str, RunId | None, object]]
"""How the message comes back: `(delivery id, run id, conversation)`."""

# [case, the second delivery, same key, claims made in all]
REDELIVERIES: list[tuple[str, Again, bool, int]] = [
    (
        "the same message, redelivered after its claim expired",
        lambda: ("msg-1", None, None),
        True,
        1,
    ),
    ("a new message", lambda: ("msg-2", None, None), False, 2),
    (
        "the same message, another part",
        lambda: ("msg-1", *reversed(delivered(ROHAN, None, None, "msg-1", part=2))),  # type: ignore[misc]
        False,
        2,
    ),
]


@pytest.mark.discharges("AHC-0053", "AHC-0074", "P-FNOL", "op:register_claim")
@pytest.mark.parametrize(
    ("case", "again", "same_key", "claims"), REDELIVERIES, ids=[r[0] for r in REDELIVERIES]
)
async def test_a_crash_before_the_checkpoint_does_not_register_a_claim_twice(
    case: str, again: Again, same_key: bool, claims: int
) -> None:
    world = live()
    clock = Monotonic()
    ledger = InMemoryRequests(now=clock)
    store = InMemoryCheckpointStore()
    server = project(world, scopes=SCOPES, authorise=as_policyholder, unknown_record="result")
    async with connect(server, requests=InMemoryRequests()) as projected:
        tools = Keys(Entities(projected, entities_of(world)))
        dying = ep.build(
            llm=ScriptedClient(script()), tools=tools, store=Dies(), deliveries=Gone(ledger)
        )  # type: ignore[arg-type]
        with pytest.raises(Crash):
            await dying.handle(REPORT, identity=me(), delivery_id="msg-1")
        assert world.count("register_claim") == 1, "the effect landed before the crash"

        clock.at += CLAIM_TTL_S + 1  # the dead process's claim lapses
        delivery, run_id, conversation = again()
        restarted = ep.build(
            llm=ScriptedClient(script()), tools=tools, store=store, deliveries=ledger
        )
        _, after = await restarted.handle(
            REPORT,
            identity=me(),
            delivery_id=delivery,
            run_id=run_id,
            conversation=conversation,  # type: ignore[arg-type]
        )

    (first_key, first_ref), (second_key, second_ref) = tools.sent
    assert (first_key == second_key) is same_key, case
    assert world.count("register_claim") == claims, case
    assert (first_ref == second_ref) is same_key, case
    assert await store.latest(after.conversation_id) is not None, "the retry checkpointed"


# [case, first (customer, delivery, conversation, part), second, same run]
NAMES = [
    ("the same message", (ROHAN, "m", "", None), (ROHAN, "m", "", None), True),
    ("another message", (ROHAN, "m", "", None), (ROHAN, "n", "", None), False),
    ("another part", (ROHAN, "m", "", 1), (ROHAN, "m", "", 2), False),
    ("another conversation", (ROHAN, "m", "cnv_a", None), (ROHAN, "m", "cnv_b", None), False),
    ("another policyholder", (ROHAN, "m", "", None), ("PH-1002", "m", "", None), False),
]


@pytest.mark.discharges("AHC-0053", "AHC-0074")
@pytest.mark.parametrize(("case", "one", "two", "same"), NAMES, ids=[n[0] for n in NAMES])
def test_a_delivered_run_is_named_by_its_message(
    case: str,
    one: tuple[str, str, str, int | None],
    two: tuple[str, str, str, int | None],
    same: bool,
) -> None:
    def named(who: str, delivery: str, conversation: str, part: int | None) -> RunId | None:
        held = None
        if conversation:
            held = Conversation(conversation_id=ConversationId(conversation), customer_id=who)
        return delivered(who, held, None, delivery, part=part)[1]

    first, second = named(*one), named(*two)
    assert first is not None and first.startswith("run_") and len(first) == 20
    assert (first == second) is same, case


@pytest.mark.discharges("AHC-0053")
def test_without_a_delivery_or_with_a_chosen_run_nothing_is_named() -> None:
    assert delivered(ROHAN, None, None, None) == (None, None)
    chosen = RunId("run_chosen")
    assert delivered(ROHAN, None, chosen, "msg-1") == (None, chosen)
