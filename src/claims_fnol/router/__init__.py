"""Whether the model is needed at all — this insurer's rules, per turn, outside the loop.

The `Route` types are the harness's; the rules are this agent's, written from
the AOAS: its `refuses` list, its `escalation.on_request` rules, and its
`intents` with their `examples` (the examples are this router's test set,
`tests/test_aoas_intents.py`).

**A handoff is checked before a refusal** — the one place this router departs
from the clothing agent's order. P-CONCERNS says that when a conversation goes
to a person, what they are handed lists every concern, *the declined ones too*,
and that a concern outside the agent's routes is handed to a person, never left
unmentioned. A message that asks for a tow *and* to add a driver would be
refused on the driver and the tow left unmentioned if refusals came first
(FINDINGS F-6). An injury is a person's at once, whatever else the message asks.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import assert_never

from agent_harness import telemetry as tel

from claims_fnol.contracts import Agentic, Direct, Escalate, Intent, Refuse, Route
from claims_fnol.contracts.reading import claim_ids, policy_ids, references

_MONEY = r"(?:payments?|payouts?|settlements?|money)"
_DOCUMENTS = (
    r"(?:police\s+report|fir(?:\s+copy)?|repair\s+estimate|estimate|invoice|photos?|pictures?"
    r"|driving\s+licen[cs]e|licen[cs]e|documents?|bill|receipt|surveyor'?s?\s+report)"
)

ALTERNATIVE = (
    "I can tell you the status of your claim or policy, report a new claim, add a document "
    "to a claim, withdraw a claim not yet assessed, or release the payout of an approved "
    "claim — or pass you to a colleague."
)
"""What a refusal offers instead (AOAS `on_refusal`: name at least one thing from
`does`). The same for every refusal: each is one of `does`, in plain words."""


def _p(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern, re.I | re.S)


@dataclass(frozen=True)
class Rules:
    """Routing rules are versioned configuration, not code edited casually —
    AAC-0101 gates routing changes like model changes."""

    version: str = "v1"
    escalate: tuple[tuple[str, str, re.Pattern[str]], ...] = field(
        default_factory=lambda: (
            (
                "injury-reported",
                "someone was hurt in the incident",
                # A negated injury ("nobody was hurt") is the policyholder telling
                # us there is none — every FNOL scenario says it — so each
                # negation is ruled out where the word is.
                _p(
                    r"(?<!nobody was )(?<!nobody got )(?<!no one was )(?<!no-one was )"
                    r"(?<!wasn't )(?<!weren't )(?<!was not )(?<!were not )(?<!not )"
                    r"(?<!nobody )(?<!no one )\b(?:hurt|injured)\b"
                    r"|(?<!no )\binjur(?:y|ies)\b"
                    r"|\b(?:hospital|ambulance|bleeding|whiplash|fractured?)\b"
                ),
            ),
            (
                "asked-for-human",
                "the policyholder asked for a person",
                _p(
                    r"\b(?:speak|talk|chat)(?:ing)?\s+(?:to|with)\s+(?:a|an|the)?\s*"
                    r"(?:human|person|agent|manager|supervisor|someone|somebody"
                    r"|representative|rep|claims\s+handler|handler)\b"
                    r"|\b(?:put|get|pass|connect|transfer)\s+me\b"
                    r"|\b(?:want|need)\s+(?:a|an|the)\s+"
                    r"(?:human|person|agent|manager|supervisor|someone|somebody"
                    r"|representative|rep|claims\s+handler)\b"
                    r"|\breal\s+(?:person|human)\b|\bhuman\s+being\b|\bescalate\b"
                ),
            ),
            (
                "outside-scope",
                "roadside assistance cannot be arranged here; a person can take it",
                # AOAS `deferred`: roadside_assistance. Deferred is not refused —
                # the AOAS says a person can take it — so it is a handoff.
                _p(
                    r"\btow(?:ing)?(?:\s+truck)?\b|\broadside\b|\bbroken?\s+down\b"
                    r"|\bbreak\s*down\b|\bflat\s+tyre\b|\bflat\s+tire\b|\bjump\s*start\b"
                    r"|\bout\s+of\s+fuel\b|\blocked\s+out\b"
                ),
            ),
        )
    )
    refuse: tuple[tuple[str, str, re.Pattern[str]], ...] = field(
        default_factory=lambda: (
            (
                "R-OTHER-HOLDER",
                "I can only talk about the policies and claims you hold",
                _p(
                    r"\b(?:my\s+(?:brother|sister|friend|father|mother|dad|mum|mom|son|daughter"
                    r"|wife|husband|neighbou?r|colleague|cousin|uncle|aunt|boss|partner)"
                    r"|someone\s+else|somebody\s+else|another\s+(?:person|policyholder|customer))"
                    r"(?:'s|’s)\s+(?:\w+\s+)?(?:claim|policy|car|vehicle|insurance|bike)\b"
                ),
            ),
            (
                "R-COVERAGE",
                "I cannot say whether a claim is covered or what it pays — only the "
                "assessment decides that",
                # Asking for the decision, never the noun: "what cover do I have"
                # is policy status, which may be told (the AOAS's line, 8 Oct).
                _p(
                    r"\b(?:will|would)\s+(?:my|the|this|it|that)\b[^.?!]{0,30}?"
                    r"\b(?:be\s+)?(?:approved|covered|accepted|paid\s+out|rejected)\b"
                    r"|\bhow\s+much\s+(?:will|would|do|can|could)\s+(?:i|we)\s+(?:get|receive)\b"
                    r"|\b(?:is|are)\s+(?:a|an|the|my|this|that|it)?\s*(?:\w+\s+){0,2}covered\b"
                    r"|\bam\s+i\s+covered\b|\bdoes\s+my\s+(?:policy|insurance|cover)\s+cover\b"
                ),
            ),
            (
                "R-LIABILITY",
                "I cannot judge who caused an incident; that is the assessor's to look at",
                _p(
                    r"\b(?:was|is|were)\s+(?:the\s+other\s+(?:driver|party|car|side)|he|she|they"
                    r"|i|it|we)\b[^.?!]{0,20}\bat\s+fault\b"
                    r"|\b(?:confirm|tell\s+me|say|decide|agree)\b[^.?!]{0,30}"
                    r"\b(?:fault|to\s+blame|liable)\b"
                    r"|\bwho(?:'s|\s+is|\s+was)\s+(?:at\s+fault|to\s+blame|liable)\b"
                ),
            ),
            (
                "R-POLICY-CHANGE",
                "I cannot change a policy's cover or its drivers, or cancel a policy, here",
                _p(
                    r"\badd\b[^.?!]{0,30}\b(?:as\s+(?:a\s+|an\s+)?(?:named\s+)?)?driver\b"
                    r"|\bremove\b[^.?!]{0,30}\bdriver\b"
                    r"|\bcancel\s+(?:my|the|this)\s+(?:\w+\s+)?(?:policy|insurance)\b"
                    r"|\b(?:upgrade|downgrade|switch)\b[^.?!]{0,20}"
                    r"\b(?:comprehensive|third[- ]party|cover|policy)\b"
                    r"|\bchange\s+(?:my|the)\s+(?:cover|policy)\b"
                ),
            ),
            (
                "R-FRAUD",
                "I cannot judge whether anyone is acting fraudulently",
                _p(
                    r"\b(?:do|does|would|can)\s+you\s+think\b[^.?!]{0,40}"
                    r"\b(?:scam|fraud|cheat|con(?:ning)?\s+me)"
                    r"|\b(?:is|was|are)\s+(?:this|that|it|he|she|they|the\s+\w+(?:\s+\w+)?)\s+"
                    r"(?:a\s+)?(?:scam(?:ming)?|fraud(?:ulent)?)\b"
                ),
            ),
            (
                "R-LEGAL",
                "I cannot give legal advice",
                _p(
                    r"\b(?:sue|suing|lawsuit|lawyer|solicitor|attorney|legal\s+(?:advice|action)"
                    r"|consumer\s+court|take\s+(?:them|him|her)\s+to\s+court)\b"
                ),
            ),
        )
    )
    """The AOAS `refuses` list, each with the id it enforces."""
    intents: tuple[tuple[Intent, re.Pattern[str]], ...] = field(
        default_factory=lambda: (
            (
                Intent.WITHDRAW_CLAIM,
                _p(
                    r"\bwithdraw\b|\bcancel\s+(?:my\s+|the\s+|this\s+)?claim\b"
                    r"|\b(?:take|pull)\s+back\s+(?:my|the)\s+claim\b"
                ),
            ),
            (
                Intent.PAYOUT_REQUEST,
                # Asking for the money, not about it (T-094's lesson, carried over).
                _p(
                    rf"\b(?:release|pay\s+out|process|make|send|issue|transfer)\b[^.?!]{{0,25}}"
                    rf"\b{_MONEY}"
                    r"|\bpay\s+(?:me|out)\b|\b(?:release|pay)\s+(?:my|the|this)\s+"
                    r"(?:approved\s+)?claim\b"
                ),
            ),
            (
                Intent.PAYOUT_STATUS,
                _p(
                    rf"\b(?:where|when|what)\b[^.?!]{{0,30}}\b{_MONEY}"
                    rf"|\b(?:has|have|did|is|was)\b[^.?!]{{0,30}}\b{_MONEY}\b[^.?!]{{0,30}}"
                    r"\b(?:made|paid|come|came|through|arrived?|sent|credited|gone|go)\b"
                    rf"|\b{_MONEY}\s+status\b|\b(?:any\s+)?(?:update|news)\s+on\s+(?:the|my)\s+"
                    rf"{_MONEY}"
                ),
            ),
            (
                Intent.REPORT_CLAIM,
                _p(
                    r"\b(?:make|file|lodge|raise|register|report|open|start)\s+(?:a\s+|an\s+|the\s+)?"
                    r"(?:new\s+)?claim\b|\b(?:can|could)\s+i\s+(?:still\s+)?claim\b"
                    r"|\b(?:had\s+an?\s+accident|crash(?:ed)?|collision|stolen|theft|smashed"
                    r"|scratched|dented|vandali[sz]ed|hit\s+my\s+car|flood(?:ed)?"
                    r"|caught\s+fire|water\s+got\s+into)\b"
                ),
            ),
            (
                Intent.SEND_DOCUMENT,
                _p(
                    r"\b(?:have|got|attach|add|upload|send|sending|here'?s|here\s+is|submit)\b"
                    rf"[^.?!]{{0,40}}\b{_DOCUMENTS}\b"
                ),
            ),
            (
                Intent.CLAIM_STATUS,
                # Not when the subject is money: that is payout status (P-PAYOUT-OWED).
                _p(
                    rf"^(?!.*\b{_MONEY}\b).*"
                    r"\b(?:where\s+is|where'?s|what'?s\s+happening|what\s+is\s+happening|status"
                    r"|latest|any\s+news|update|progress|got\s+to|has\s+the\s+assessor"
                    r"|being\s+assessed|looked\s+at)\b"
                ),
            ),
            (
                Intent.POLICY_STATUS,
                _p(
                    r"\b(?:policy|insurance|cover)\b[^.?!]{0,40}"
                    r"\b(?:active|in\s+force|valid|lapsed|expired)\b"
                    r"|\bwhat\s+(?:kind\s+of\s+|type\s+of\s+)?cover\b"
                    r"|\bwhat(?:'s|\s+is)\s+(?:my|the)\s+excess\b"
                ),
            ),
        )
    )


DIRECT_HANDLERS: dict[Intent, str] = {
    Intent.CLAIM_STATUS: "claim_status",
    Intent.PAYOUT_STATUS: "payout_status",
    Intent.POLICY_STATUS: "policy_status",
}
"""Only read-only, single-step intents resolve without the model (P-DIRECT-READS)."""

KEYED_BY: dict[Intent, str] = {
    Intent.CLAIM_STATUS: "CLM-",
    Intent.PAYOUT_STATUS: "CLM-",
    Intent.POLICY_STATUS: "POL-",
}


def route(text: str, *, rules: Rules | None = None) -> Route:
    """Classify one turn. Handoffs first (see the module), then refusals, then intents."""
    rules = rules or Rules()
    with tel.span("agent.route", **{tel.ROUTE_KIND: "pending"}) as span:
        decision = _decide(text, rules)
        span.set_attribute(tel.ROUTE_KIND, decision.kind)
        span.set_attribute(tel.ROUTE_REASON, _reason_of(decision))
        span.set_attribute("agent.router.rules_version", rules.version)
        return decision


def _decide(text: str, rules: Rules) -> Route:
    for rule_id, reason, pattern in rules.escalate:
        if pattern.search(text):
            return Escalate(reason=reason, rule_id=rule_id, tier=1)

    for rule_id, reason, pattern in rules.refuse:
        if pattern.search(text):
            return Refuse(reason=reason, alternative=ALTERNATIVE, rule_id=rule_id)

    matched = intents_of(text, rules)
    if len(matched) != 1 or matched[0] not in DIRECT_HANDLERS:
        return Agentic(goal=text.strip(), candidate_intents=matched)

    # P-DIRECT: exactly one intent and exactly one claim or policy — of the kind
    # the handler reads. Several, or none, is the loop's.
    intent = matched[0]
    named = references(text)
    if len(named) != 1 or not next(iter(named)).startswith(KEYED_BY[intent]):
        return Agentic(goal=text.strip(), candidate_intents=matched)
    return Direct(intent=intent, handler=DIRECT_HANDLERS[intent], args={"id": named.pop()})


def intents_of(text: str, rules: Rules | None = None) -> tuple[Intent, ...]:
    """Every intent the text carries, in the rules' order."""
    rules = rules or Rules()
    return tuple(intent for intent, pattern in rules.intents if pattern.search(text))


def _reason_of(decision: Route) -> str:
    match decision:
        case Refuse() | Escalate():
            return decision.reason
        case Direct():
            return f"unambiguous {decision.intent!s} with one reference"
        case Agentic():
            return "ambiguous, multi-intent or unmodelled"
        case _:
            assert_never(decision)


def refusal_text(decision: Refuse) -> str:
    """What a refused policyholder is told — the reason, and what can be done instead."""
    if decision.alternative:
        return f"I am sorry — {decision.reason}. {decision.alternative}"
    return f"I am sorry — {decision.reason}."


# Below everything it reads: the concerns module imports `route` from here.
from claims_fnol.router.concerns import VIA, Owed, concerns, owed  # noqa: E402

__all__ = [
    "ALTERNATIVE",
    "DIRECT_HANDLERS",
    "VIA",
    "Owed",
    "Rules",
    "claim_ids",
    "concerns",
    "intents_of",
    "owed",
    "policy_ids",
    "refusal_text",
    "route",
]
