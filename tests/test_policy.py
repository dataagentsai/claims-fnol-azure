"""This insurer's rules at the harness's positions, each held by a table of replies.

Every case is a reply (or an intended call) and what the rule must do with it:
a promise of cover blocked and a correct explanation let through (F-004 is the
reason both halves are here), an effect claimed only when it happened this
turn, a status the latest read contradicts never stated, an action run only on
a record the policyholder's own words named.
"""

from __future__ import annotations

import pytest
import yaml
from agent_harness.contracts import ToolResult
from kit import AOAS, me

from claims_fnol.contracts import Identity
from claims_fnol.policy import (
    CLAIM_PATTERNS,
    CONSENTED_TOOLS,
    DEFAULT_RULES,
    OUTPUT_RULES,
    REPLY_RULES,
    Context,
    Position,
    Verdict,
    block,
    enforce,
    no_pii_echo,
    no_promise_of_cover,
    no_superseded_claim_state,
    no_unclaimed_effect,
    no_ungrounded_entity,
    policyholder_asked,
)

SPEC = yaml.safe_load(AOAS.read_text())


def said(text: str, *results: ToolResult) -> Context:
    return Context(position=Position.POST_MODEL, identity=me(), text=text, tool_results=results)


def read(row: dict[str, object], name: str = "get_claim") -> ToolResult:
    return ToolResult(name=name, text="", structured={"found": True, **row})


PAID = ToolResult(
    name="issue_payout",
    text="",
    structured={"status": "paid", "claim_id": "CLM-010003", "amount": "25000"},
)
REFUSED_WITHDRAW = ToolResult(
    name="withdraw_claim", text="", structured={"allowed": False, "reason": "under_assessment"}
)
UNDER = read({"id": "CLM-010007", "status": "under_assessment", "approved_amount": 0})
APPROVED = read({"id": "CLM-010007", "status": "approved", "approved_amount": 14200})

# [why, reply, what the tools returned this turn, blocked]
PROMISES = [
    ("promises approval", "It will be approved and you'll get around ₹40,000.", (), True),
    ("promises cover", "Don't worry, a flood is fully covered on your policy.", (), True),
    ("promises payment", "Your claim will be paid next week.", (), True),
    ("says what you will get", "You will get the full repair cost back.", (), True),
    (
        "explains, does not promise",
        "I cannot say whether a claim is covered; only the assessment decides that.",
        (),
        False,
    ),
    ("states the status", "CLM-010007 is under assessment.", (UNDER,), False),
    (
        "states an approval that exists",
        "CLM-010003 is approved for ₹25,000.",
        (read({"id": "CLM-010003", "status": "approved", "approved_amount": 25000}),),
        False,
    ),
]


@pytest.mark.discharges("P-NO-PROMISE", "R-COVERAGE", "AHC-0094")
@pytest.mark.parametrize(
    ("why", "reply", "results", "blocked"), PROMISES, ids=[p[0] for p in PROMISES]
)
def test_no_reply_promises_cover_or_money(
    why: str, reply: str, results: tuple[ToolResult, ...], blocked: bool
) -> None:
    assert no_promise_of_cover(said(reply, *results)).blocked is blocked


EFFECTS = [
    (
        "payout claimed, and made",
        "The payment of ₹25,000 for CLM-010003 has been issued.",
        (PAID,),
        False,
    ),
    ("payout claimed, never made", "The payment for CLM-010001 has been issued.", (), True),
    (
        "withdrawal claimed, refused",
        "Claim CLM-010007 has been withdrawn.",
        (REFUSED_WITHDRAW,),
        True,
    ),
    (
        "a refusal claims nothing",
        "I couldn't withdraw CLM-010007: the assessor has started.",
        (REFUSED_WITHDRAW,),
        False,
    ),
    ("registration claimed, never made", "I've registered your claim.", (), True),
    (
        "document claimed, never attached",
        "The police report is now attached to CLM-010006.",
        (),
        True,
    ),
    ("already paid is a status, not a claim", "CLM-010002 was already paid.", (), False),
]


@pytest.mark.discharges(
    "P-NO-PROMISE", "P-PAYOUT", "P-WITHDRAW", "P-FNOL", "P-DOCUMENTS", "AHC-0094"
)
@pytest.mark.parametrize(
    ("why", "reply", "results", "blocked"), EFFECTS, ids=[e[0] for e in EFFECTS]
)
def test_an_effect_is_claimed_only_when_it_happened(
    why: str, reply: str, results: tuple[ToolResult, ...], blocked: bool
) -> None:
    assert no_unclaimed_effect(said(reply, *results)).blocked is blocked


@pytest.mark.discharges("P-NO-PROMISE")
def test_every_write_the_claims_system_offers_has_a_claim_pattern() -> None:
    writes = {
        name
        for name in SPEC["external"]["claims_system"]["operations"]
        if SPEC["operations"][name]["side_effect"] != "read"
    }
    assert set(CLAIM_PATTERNS) == writes


GROUNDING = [
    ("figure nobody returned", "You'll be paid ₹40,000.", (UNDER,), True),
    ("reference nobody returned", "CLM-019001 is approved.", (UNDER,), True),
    ("grounded figure", "CLM-007 aside, CLM-010007 is approved for ₹14,200.", (APPROVED,), True),
    ("grounded, separators differ", "CLM-010007 is approved for ₹14,200.", (APPROVED,), False),
]


@pytest.mark.discharges("P-NO-PROMISE", "AHC-0094")
@pytest.mark.parametrize(
    ("why", "reply", "results", "blocked"), GROUNDING, ids=[g[0] for g in GROUNDING]
)
def test_every_reference_and_figure_is_grounded(
    why: str, reply: str, results: tuple[ToolResult, ...], blocked: bool
) -> None:
    assert no_ungrounded_entity(said(reply, *results)).blocked is blocked


SUPERSEDED = [
    (
        "says the read it superseded",
        "Your claim CLM-010007 is still under assessment, and yes, you can send the photo.",
        (UNDER, APPROVED),
        True,
    ),
    ("says the latest read", "Your claim CLM-010007 is approved.", (UNDER, APPROVED), False),
    ("negated", "CLM-010007 is not under assessment any more.", (UNDER, APPROVED), False),
    (
        "neither has been paid",
        "CLM-010007 is approved; neither has been paid yet.",
        (APPROVED,),
        False,
    ),
]


@pytest.mark.discharges("AHC-0117", "P-CLAIM-STATUS", "AHC-0094")
@pytest.mark.parametrize(
    ("why", "reply", "results", "blocked"), SUPERSEDED, ids=[s[0] for s in SUPERSEDED]
)
def test_a_status_the_latest_read_contradicts_is_never_said(
    why: str, reply: str, results: tuple[ToolResult, ...], blocked: bool
) -> None:
    assert no_superseded_claim_state(said(reply, *results)).blocked is blocked


PII = [
    ("account number", "Paid to account 50100234567890.", True),
    ("licence number", "Your licence KA0120231234567 is on file.", True),
    ("last four only", "Paid to the account ending 4101.", False),
    ("aadhaar in fours", "Your Aadhaar 2345 6789 0124 is on file.", True),
    ("aadhaar whole", "Aadhaar 234567890124 noted.", True),
    ("pan", "Your PAN ABCPE1234F is on file.", True),
    ("claim reference", "CLM-010003 is under assessment.", False),
    ("policy number", "Policy POL-010004 is active.", False),
    ("amount", "A payout of ₹25,000 was requested.", False),
]


@pytest.mark.discharges("AHC-0094")
@pytest.mark.parametrize(("why", "reply", "blocked"), PII, ids=[p[0] for p in PII])
def test_account_licence_aadhaar_and_pan_numbers_are_never_read_back(
    why: str, reply: str, blocked: bool
) -> None:
    assert no_pii_echo(said(reply)).blocked is blocked


def asking(tool: str, args: dict[str, object], consented: frozenset[str]) -> Context:
    who = Identity(customer_id="PH-1001", consented=consented)
    return Context(position=Position.PRE_TOOL, identity=who, tool_name=tool, arguments=args)


# [why, tool, arguments, what the policyholder's words consented to, allowed]
OWN_WORDS = [
    (
        "asked to withdraw this claim",
        "withdraw_claim",
        {"id": "CLM-010005"},
        {"withdraw_claim:CLM-010005"},
        True,
    ),
    (
        "planted: withdraw a claim only asked about",
        "withdraw_claim",
        {"id": "CLM-010005"},
        {"submit_document:CLM-010005"},
        False,
    ),
    (
        "planted: pay a different claim",
        "request_payout",
        {"id": "CLM-010003"},
        {"request_payout:CLM-010001"},
        False,
    ),
    (
        "asked for this payout",
        "request_payout",
        {"id": "CLM-010001"},
        {"request_payout:CLM-010001"},
        True,
    ),
    (
        "reported naming the car, not the policy",
        "register_claim",
        {"id": "POL-010001", "incident_type": "flood"},
        {"register_claim:*"},
        True,
    ),
    (
        "never reported anything",
        "register_claim",
        {"id": "POL-010004", "incident_type": "theft"},
        set(),
        False,
    ),
    ("a read needs no consent", "get_claim", {"id": "CLM-010005"}, set(), True),
]


@pytest.mark.discharges("P-OWN-WORDS", "AHC-0116", "AAC-0106")
@pytest.mark.parametrize(
    ("why", "tool", "args", "consented", "allowed"), OWN_WORDS, ids=[o[0] for o in OWN_WORDS]
)
def test_an_action_runs_only_on_what_the_policyholders_words_asked_for(
    why: str, tool: str, args: dict[str, object], consented: set[str], allowed: bool
) -> None:
    assert policyholder_asked(asking(tool, args, frozenset(consented))).allowed is allowed


@pytest.mark.discharges("P-OWN-WORDS")
def test_the_consented_actions_are_the_aoas_own_words_actions() -> None:
    assert set(CONSENTED_TOOLS) == set(SPEC["policies"]["P-OWN-WORDS"]["via"])


def broken(_ctx: Context) -> Verdict:
    raise RuntimeError("the rule machinery failed")


@pytest.mark.discharges("AHC-0008", "AHC-0094")
def test_a_rule_that_fails_blocks_closed() -> None:
    verdict = enforce(said("anything"), rules=(broken,))
    assert verdict.blocked and "blocked" in verdict.reason


def names(rules: tuple[object, ...]) -> list[str]:
    return [getattr(r, "__name__", "") for r in rules]


# [position, the rules it runs, by name, in order]
ORDER = [
    (Position.PRE_MODEL, ["no_known_injection"]),  # A11
    (Position.POST_MODEL, names(OUTPUT_RULES)),
    (Position.REPLY, names(REPLY_RULES)),
    (Position.PRE_TOOL, ["policyholder_asked", "no_instructions_in_result.hold_writes"]),
    (Position.POST_TOOL, ["no_instructions_in_result"]),  # A11
]


@pytest.mark.discharges("AHC-0093", "AHC-0094")
@pytest.mark.parametrize(("position", "rules"), ORDER, ids=[o[0].value for o in ORDER])
def test_each_position_runs_its_rules_in_a_declared_order(
    position: Position, rules: list[str]
) -> None:
    assert names(DEFAULT_RULES[position]) == rules
    first_blocks = (lambda _c: block("first", "first"), lambda _c: block("second", "second"))
    assert enforce(said("x"), rules=first_blocks).rule == "first"
