"""A13 — a kill switch per agent: `agent.enabled`, read through the config port.

The switch is the harness's (`agent_harness.entrypoint.switch.Switched`, tested
there on a stand-in agent); the composition root wraps this agent in it
(`claims_fnol_app.compose`). Here it is held on this agent, with the model
scripted and a payout approval waiting at the handler desk, through one table
of turns while the owner flips the key:

    enabled                          the model is called
    disabled                         no model call; the paused reply
    disabled                         an approval decision at the desk still resolves
    enabled again, after the TTL     the model is called again
"""

from __future__ import annotations

import pytest
from agent_harness.config.settings import Cached
from agent_harness.entrypoint.switch import ENABLED, RULE, Switched
from evals.durable import approvals_for
from kit import agent, claims_system, me, says

from claims_fnol.binding import PAUSED
from claims_fnol.contracts import IdempotencyKey, Refused, RunId

TTL_S = 5.0
HELLO = "Any news on CLM-010005? I also have the windscreen invoice for it."

# (step, agent.enabled in the store, seconds since the last step, the model is
#  asked, the reply, a handler decides the waiting approval in this step)
STEPS = [
    ("enabled: the model is called", "true", 0, True, "CLM-010005 is registered.", False),
    ("disabled: no model call, the paused reply", "false", TTL_S, False, PAUSED, False),
    ("disabled: an approval decision still resolves", "false", 1, False, PAUSED, True),
    (
        "enabled again, after the TTL: the model is called",
        "true",
        TTL_S,
        True,
        "Still registered.",
        False,
    ),
]
ANSWERS = [
    says("", ("get_claim", {"id": "CLM-010005"})),
    says("CLM-010005 is registered."),
    says("", ("get_claim", {"id": "CLM-010005"})),
    says("Still registered."),
]


@pytest.mark.discharges("AAC-0055", "AHC-0003", "P-APPROVAL-QUEUE")
async def test_the_kill_switch_pauses_turns_and_leaves_the_desk_alone() -> None:
    now = [0.0]
    held = {"agent.enabled": "true"}
    settings = Cached(
        [ENABLED],
        lambda names: {n: held[n] for n in names if n in held},
        ttl_s=TTL_S,
        clock=lambda: now[0],
    )
    async with (
        claims_system() as tools,
        approvals_for(tools) as waits,
        agent(ANSWERS, approvals=waits.approvals) as (built, llm),
    ):
        asked = await waits.approvals.request(
            action="issue_payout",
            args={"claim_id": "CLM-010004"},
            identity=me(),
            idempotency_key=IdempotencyKey(run_id=RunId("r-switch"), step=0, iteration=0),
        )
        switched = Switched(built, settings, reply=PAUSED)
        conversation = None
        for step, value, elapsed, model, reply, decide in STEPS:
            held["agent.enabled"] = value
            now[0] += elapsed
            calls = len(llm.calls)
            result, conversation = await switched.handle(
                HELLO, identity=me(), conversation=conversation
            )
            assert (len(llm.calls) > calls) is model, step
            assert getattr(result, "reply", "") == reply, step
            if not model:
                assert isinstance(result, Refused) and result.rule_id == RULE, step
                assert conversation.messages[-2].content == HELLO, "the message is kept"
            if decide:
                assert [a.id for a in await waits.approvals.pending()] == [asked.id]
                decided = await waits.desk.decide(asked.id, granted=True, by="claims-handler-3")
                assert decided.granted, step
                assert await waits.approvals.pending() == (), step
