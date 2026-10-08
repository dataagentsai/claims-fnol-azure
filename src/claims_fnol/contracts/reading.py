"""This insurer's reading of text: what a reference looks like, which statuses a sentence asserts.

The fold (dashes, odd spaces, case) is the harness's
(`agent_harness.contracts.reading.normalised`); the shapes are the AOAS's —
`claim.id` is `CLM-[0-9]{6}` and `policy.id` is `POL-[0-9]{6}` — and the status
grammar is `claim_status`'s. Registered with the harness at import
(`VOCABULARY`), so its superseded-state rule and the watch read the same shapes
the router and the consent list do.
"""

from __future__ import annotations

import re

from agent_harness.contracts.reading import Vocabulary, normalised

from claims_fnol.contracts.domain import ClaimStatus

CLAIM_ID = re.compile(r"\b(CLM-\d{6})\b")
POLICY_ID = re.compile(r"\b(POL-\d{6})\b")
REFERENCE = re.compile(r"\b((?:CLM|POL)-\d{6})\b")
"""A claim or policy reference in its canonical spelling, and nothing else."""

_SPOKEN = re.compile(r"\b(clm|pol)\s?-?\s?(\d{6})\b", re.I)
"""A reference as people and models write it, after folding: any case, the
hyphen optional ("clm 100231", "CLM100231")."""


def references(text: str) -> set[str]:
    """Every claim or policy reference in `text`, each in canonical spelling."""
    return {f"{p.upper()}-{d}" for p, d in _SPOKEN.findall(normalised(text))}


def claim_ids(text: str) -> set[str]:
    return {r for r in references(text) if r.startswith("CLM-")}


def policy_ids(text: str) -> set[str]:
    return {r for r in references(text) if r.startswith("POL-")}


_STATES = sorted((s.value.replace("_", "[ _]") for s in ClaimStatus), key=len, reverse=True)
STATUS_CLAIM = re.compile(
    r"\b(is|was|are|were|has|have|had|been|be)\b((?:\s+[\w']+){0,2}?)\s+("
    + "|".join(_STATES)
    + r")\b",
    re.I,
)
"""A claim status *asserted* of something: "is under assessment", "has been
paid". The words between may not negate it."""
_NEGATION = re.compile(r"\b(not|never|no longer|cannot)\b|n't", re.I)
_NEGATED_BEFORE = re.compile(r"\b(?:neither|nor|none(?:\s+of\s+\w+)?|nothing)\s*$", re.I)
"""A subject that negates what follows: "neither has been paid" asserts nothing paid."""
_PAST = frozenset({"was", "were", "had"})
_SPOKEN_STATES = {"being assessed": "under_assessment", "with the assessor": "under_assessment"}
_SPOKEN_CLAIM = re.compile(
    r"\b(is|are)\b((?:\s+[\w']+){0,1}?)\s+(" + "|".join(_SPOKEN_STATES) + r")\b", re.I
)


def claimed_states(sentence: str, *, present_only: bool = False) -> set[str]:
    """The claim statuses a sentence asserts, as `ClaimStatus` values."""
    said: set[str] = set()
    for match in STATUS_CLAIM.finditer(sentence):
        verb, between, word = match.group(1).lower(), match.group(2), match.group(3)
        if _NEGATION.search(verb + between) or verb == "be":
            continue
        if _NEGATED_BEFORE.search(sentence[: match.start()]):
            continue
        if present_only and verb in _PAST:
            continue
        said.add(word.lower().replace(" ", "_"))
    for _verb, between, phrase in _SPOKEN_CLAIM.findall(sentence):
        if not _NEGATION.search(between):
            said.add(_SPOKEN_STATES[phrase.lower()])
    return said


VOCABULARY = Vocabulary(
    identifier=REFERENCE, identifiers=references, claimed_states=claimed_states
)
"""What the harness's checks read as an identifier and a claimed state."""

__all__ = [
    "CLAIM_ID",
    "POLICY_ID",
    "REFERENCE",
    "STATUS_CLAIM",
    "VOCABULARY",
    "claim_ids",
    "claimed_states",
    "normalised",
    "policy_ids",
    "references",
]
