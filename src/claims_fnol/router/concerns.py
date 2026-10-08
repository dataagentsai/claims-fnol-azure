"""The separate concerns a message raises, and what the turn owes each (AHC-0118, P-CONCERNS).

A clause is a concern; a concern this agent can act on, naming one claim or
policy, is owed a call to one of its intent's operations (`via`). The loop's
owed check is the harness's; which operations deal with which intent is the
AOAS's `intents.*.via`, copied here and held to it by a test.
"""

from __future__ import annotations

import re
from typing import NamedTuple

from claims_fnol.contracts import Agentic, Direct, Intent
from claims_fnol.contracts.reading import REFERENCE, references
from claims_fnol.router import route

_CLAUSE = re.compile(
    r"(?<=[.?!;])\s+|,\s*(?:and\s+)?|\s+and\s+(?=(?:i|i'm|i've|is|was|my|the|it|can|could"
    r"|please|where|what|when|how|why|also)\b)",
    re.I,
)


def concerns(text: str) -> tuple[str, ...]:
    """The separate things a message raises, one per clause. A clause is a
    concern when it names a claim or policy or runs to three words."""
    parts = (part.strip(" ,.;") for part in _CLAUSE.split(text))
    return tuple(part for part in parts if REFERENCE.search(part) or len(part.split()) >= 3)


VIA: dict[str, tuple[str, ...]] = {
    Intent.CLAIM_STATUS: ("get_claim", "list_claims"),
    Intent.PAYOUT_STATUS: ("get_claim", "list_claims"),
    Intent.POLICY_STATUS: ("get_policy", "list_policies"),
    Intent.REPORT_CLAIM: ("register_claim",),
    Intent.SEND_DOCUMENT: ("submit_document",),
    Intent.WITHDRAW_CLAIM: ("withdraw_claim",),
    Intent.PAYOUT_REQUEST: ("request_payout",),
}
"""The AOAS intents' `via`: the operations that deal with each."""


class Owed(NamedTuple):
    """One concern the turn owes an outcome: its words, its record, what deals with it."""

    concern: str
    record: str
    tools: tuple[str, ...]


def owed(text: str) -> tuple[Owed, ...]:
    """The concerns of a several-concern message the loop must deal with.

    Only where a clause names one record and carries one intent this agent can
    act on. A single concern is the whole turn and needs no list.
    """
    clauses = concerns(text)
    if len(clauses) < 2:
        return ()
    out = []
    for clause in clauses:
        decision = route(clause)
        if isinstance(decision, Direct):
            intents: tuple[str, ...] = (decision.intent,)
        elif isinstance(decision, Agentic):
            intents = decision.candidate_intents
        else:
            intents = ()
        ids = references(clause)
        if len(intents) == 1 and len(ids) == 1 and intents[0] in VIA:
            out.append(Owed(clause, next(iter(ids)), VIA[intents[0]]))
    return tuple(out)


__all__ = ["VIA", "Owed", "concerns", "owed"]
