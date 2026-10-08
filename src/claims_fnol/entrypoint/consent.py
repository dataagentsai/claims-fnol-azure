"""What the policyholder has authorised, read from what they said (P-OWN-WORDS, AHC-0116).

The pre-tool rule `policy.policyholder_asked` lets an action run only on a record
the policyholder asked for it on. This works out what they asked, from their
own messages and never from a tool's result, so text planted in a claim note
cannot add to it. Two ways in: **asked** (a message carries the action's intent,
by the router's own patterns, and names the record — or, naming none, the
records named anywhere in the conversation), and **confirmed** (an action the
rule refused is held awaiting confirmation, and a plain yes authorises exactly it).
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable

from agent_harness.state import Conversation
from agent_harness.state.facts import Facts

from claims_fnol import router
from claims_fnol.contracts import Identity, Intent
from claims_fnol.contracts.reading import claim_ids, policy_ids
from claims_fnol.policy import ANY_POLICY

BY_INTENT: dict[str, Intent] = {
    "withdraw_claim": Intent.WITHDRAW_CLAIM,
    "submit_document": Intent.SEND_DOCUMENT,
    "request_payout": Intent.PAYOUT_REQUEST,
    "register_claim": Intent.REPORT_CLAIM,
}
"""Each consented action and the intent that asks for it (AOAS `intents.*.via`)."""

AFFIRMATIVE = re.compile(
    r"^\s*(yes|yeah|yep|sure|ok(ay)?|please do|go ahead|confirm(ed)?|do it)\b", re.I
)
CONFIRM = "confirm"


def granting(
    identity: Identity, conversation: Conversation, text: str, rules: router.Rules
) -> Identity:
    """The turn's identity, carrying what the policyholder has consented to."""
    return identity.model_copy(update={"consented": consented(conversation, text, rules)})


def _records(tool: str, message: str, said: list[str]) -> set[str]:
    if tool == "register_claim":
        named = policy_ids(message)
        return named or {ANY_POLICY}
    return claim_ids(message) or {c for s in said for c in claim_ids(s)}


def consented(conversation: Conversation, text: str, rules: router.Rules) -> frozenset[str]:
    said = [m.content for m in conversation.messages if m.role == "user"]
    if not said or said[-1] != text:
        said.append(text)
    granted: set[str] = set()
    for message in said:
        intents = set(router.intents_of(message, rules))
        for tool, intent in BY_INTENT.items():
            if intent in intents:
                granted |= {f"{tool}:{record}" for record in _records(tool, message, said)}
    if AFFIRMATIVE.match(text):
        granted |= set(awaiting(conversation.facts))
    return frozenset(granted)


def awaiting(facts: Facts) -> list[str]:
    prefix = f"{CONFIRM}:"
    return [entry[len(prefix) :] for entry in facts.awaiting if entry.startswith(prefix)]


def pending(facts: Facts, attempted: Iterable[tuple[str, str]], identity: Identity) -> Facts:
    """This turn's refused actions become the ones awaiting confirmation; earlier
    ones are withdrawn, so a yes three turns later authorises nothing."""
    for entry in awaiting(facts):
        facts = facts.settled(CONFIRM, entry)
    for name, arguments in attempted:
        if name not in BY_INTENT:
            continue
        given = json.loads(arguments) if arguments.startswith("{") else {}
        entry = f"{name}:{given.get('id') or given.get('claim_id')}"
        if entry not in identity.consented and f"{name}:{ANY_POLICY}" not in identity.consented:
            facts = facts.waiting_on(CONFIRM, entry)
    return facts


__all__ = ["BY_INTENT", "consented", "granting", "pending"]
