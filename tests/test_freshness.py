"""AHC-0107: a fact an irreversible action relies on is read inside its declared window.

AOAS `claim.status` is `fresh_for: 30s` — the assessor and the payments system
write it while a conversation is open. A withdrawal planned on a read older
than that is held, the claim is read again, and the model decides on what is
true now. The mechanism is the harness's; the 30 seconds are the AOAS's.
"""

from __future__ import annotations

import pytest
import yaml
from kit import AOAS, agent, live, me, says

from claims_fnol.binding import FRESH_FOR_S

SPEC = yaml.safe_load(AOAS.read_text())


@pytest.mark.discharges("AHC-0107")
def test_the_window_is_the_aoas_window() -> None:
    assert SPEC["entities"]["claim"]["fields"]["status"]["fresh_for"] == f"{FRESH_FOR_S}s"


# [seconds between the read and the withdrawal, whether the claim is read again first]
GAPS = [(5, False), (29, False), (31, True), (45, True)]
"""All inside the turn's own 60-second clock (AHC-0096), which ends a turn on its own."""


@pytest.mark.discharges("AHC-0107", "op:withdraw_claim", "P-WITHDRAW")
@pytest.mark.parametrize(("gap", "reread"), GAPS, ids=[f"{g[0]}s" for g in GAPS])
async def test_an_irreversible_withdrawal_on_a_stale_read_reads_again_first(
    gap: int, reread: bool
) -> None:
    world = live()
    now = [1_000]
    answers = [
        says("", ("get_claim", {"id": "CLM-010005"})),
        says("", ("withdraw_claim", {"id": "CLM-010005"})),
        says("", ("withdraw_claim", {"id": "CLM-010005"})),
        says("Claim CLM-010005 has been withdrawn."),
    ]

    def clock() -> int:
        return now[0]

    async with agent(answers, world=world, clock=clock) as (built, llm):
        original = llm.complete

        async def moving(request):  # type: ignore[no-untyped-def]
            if len(llm.calls) == 1:
                now[0] += gap  # time passes between the read and the plan to withdraw
                world.rows["claim"]["CLM-010005"]["status"] = "under_assessment"  # the assessor starts
            return await original(request)

        llm.complete = moving  # type: ignore[method-assign]
        await built.handle(
            "Please withdraw claim CLM-010005, and where is CLM-010006?", identity=me()
        )
    last = llm.calls[-1].messages
    reads = sum(1 for m in last if m.role == "tool" and "source=tool:get_claim" in m.content)
    held = any(m.role == "tool" and "not run: what this run knew" in m.content for m in last)
    assert (reads >= 2) is reread and held is reread
    assert world.count("withdraw_claim") == 0, "the claims system's own precondition still holds"
