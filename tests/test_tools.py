"""The claims system as the agent's tools: declared contracts, checked arguments, classes that gate, keys that dedupe.

The tool surface is the projected AOAS operations, reached through the
harness's MCP client (`agent_harness.tools`) with this agent's scopes.
"""

from __future__ import annotations

import pytest
import yaml
from agent_harness import context as ctx
from kit import AOAS, MEERA, ROHAN, agent, claims_system, live, me, says

from claims_fnol.binding import SCOPES
from claims_fnol.contracts import IdempotencyKey, RunId, SideEffectClass

SPEC = yaml.safe_load(AOAS.read_text())
CLASSES = {
    "read": SideEffectClass.READ,
    "reversible": SideEffectClass.REVERSIBLE,
    "irreversible": SideEffectClass.IRREVERSIBLE,
}


def key(step: int = 0) -> IdempotencyKey:
    return IdempotencyKey(run_id=RunId("r-test"), step=step, iteration=0)


@pytest.mark.discharges("AHC-0036", "AHC-0039")
@pytest.mark.parametrize("operation", SPEC["external"]["claims_system"]["operations"])
async def test_every_operation_declares_its_contract_and_class(operation: str) -> None:
    async with claims_system() as tools:
        surface = await tools.list_tools(
            me().model_copy(update={"scopes": frozenset(SCOPES.values())})
        )
    spec = surface.get(operation)
    assert spec is not None and spec.input_schema.get("type") == "object"
    assert spec.side_effect is CLASSES[SPEC["operations"][operation]["side_effect"]]
    shown = {t["function"]["name"]: t for t in ctx.model_tools(surface)}  # type: ignore[index]
    assert shown[operation]["function"]["parameters"] == spec.input_schema  # type: ignore[index]


# [operation, on the policyholder's own surface]
SURFACE = [
    ("get_claim", True),
    ("list_policies", True),
    ("register_claim", True),
    ("withdraw_claim", True),
    ("issue_payout", False),  # offered: false — only a grant puts it on a surface
]


@pytest.mark.discharges("AHC-0039", "AHC-0057", "op:issue_payout", "P-APPROVER")
@pytest.mark.parametrize(("operation", "shown"), SURFACE, ids=[s[0] for s in SURFACE])
async def test_the_irreversible_payout_needs_a_grant_the_policyholder_does_not_hold(
    operation: str, shown: bool
) -> None:
    async with claims_system() as tools:
        surface = await tools.list_tools(me())
    assert (surface.get(operation) is not None) is shown


@pytest.mark.discharges("AHC-0037", "AHC-0043", "op:submit_document")
async def test_arguments_are_checked_before_the_tool_runs() -> None:
    world = live()
    answers = [
        says(
            "", ("submit_document", {"claim": "CLM-010006"})
        ),  # no id, an argument it does not take
        says("I could not attach that."),
    ]
    async with agent(answers, world=world) as (built, llm):
        await built.handle("I have the police report for CLM-010006 and CLM-010005", identity=me())
    assert world.effects == []
    error = next(m for m in llm.calls[-1].messages if m.role == "tool")
    assert "error" in error.content


# [who asks, about what, whether it is found]
OWNERSHIP = [
    (ROHAN, "CLM-010001", True),
    (ROHAN, "CLM-019001", False),  # the stranger's, answered exactly as a missing one
    (MEERA, "CLM-019001", True),
    (MEERA, "CLM-010001", False),
    (ROHAN, "CLM-999999", False),
]


@pytest.mark.discharges(
    "P-OWNERSHIP", "R-OTHER-HOLDER", "AHC-0034", "op:get_claim", "ext:claims_system"
)
@pytest.mark.parametrize(
    ("who", "claim", "found"), OWNERSHIP, ids=[f"{o[0]}-{o[1]}" for o in OWNERSHIP]
)
async def test_the_policyholders_identity_reaches_the_claims_system(
    who: str, claim: str, found: bool
) -> None:
    async with claims_system() as tools:
        result = await tools.call("get_claim", {"id": claim}, me(who), key())
    assert isinstance(result.structured, dict)
    assert (result.structured.get("found") is not False) is found
    if not found:
        assert set(result.structured) == {"found", "allowed", "reason"}, (
            "nothing about the row leaks"
        )


@pytest.mark.discharges("P-OWNERSHIP", "op:list_claims", "op:list_policies")
@pytest.mark.parametrize("listing", ["list_claims", "list_policies"])
async def test_a_listing_holds_only_the_policyholders_own_rows(listing: str) -> None:
    async with claims_system() as tools:
        result = await tools.call(listing, {}, me(), key())
    items = result.structured["items"]  # type: ignore[index]
    assert items and all(row["policyholder_id"] == ROHAN for row in items)


@pytest.mark.discharges("AHC-0074", "ext:claims_system", "op:submit_document")
async def test_a_repeated_write_under_one_key_lands_once() -> None:
    world = live()
    async with claims_system(world) as tools:
        args = {"id": "CLM-010006", "document_type": "police_report"}
        first = await tools.call("submit_document", args, me(), key(3))
        again = await tools.call("submit_document", args, me(), key(3))
    assert world.count("submit_document") == 1
    assert first.structured == again.structured


@pytest.mark.discharges(
    "AHC-0104", "op:withdraw_claim", "op:submit_document", "P-WITHDRAW", "P-DOCUMENTS"
)
async def test_writes_planned_together_run_one_at_a_time_in_order() -> None:
    world = live()
    both = says(
        "",
        ("submit_document", {"id": "CLM-010006", "document_type": "police_report"}),
        ("withdraw_claim", {"id": "CLM-010005"}),
    )
    async with agent([both, says("Done.")], world=world) as (built, _):
        await built.handle(
            "Here's the police report for CLM-010006, and please withdraw claim CLM-010005",
            identity=me(),
        )
    assert world.effects == [("submit_document", "CLM-010006"), ("withdraw_claim", "CLM-010005")]
