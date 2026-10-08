"""This insurer's rules, at the harness's positions.

L7. The positions, the enforcement and the fail-closed rule are the harness's
(`agent_harness.policy`). What is checked at each is this agent's: the effects
its replies may not claim, the promises of cover they may not make
(P-NO-PROMISE), the entities they must ground, the actions a policyholder's own
words must have asked for (P-OWN-WORDS). Registered as the default for every
position at import.
"""

from __future__ import annotations

import re
from dataclasses import replace

from agent_harness.contracts import ToolResult
from agent_harness.policy import (
    ALLOW,
    SAFE_REPLY,
    Context,
    Position,
    Rule,
    Verdict,
    block,
    enforce,
    no_superseded_state,
    use_default_rules,
)

CLAIM_PATTERNS: dict[str, re.Pattern[str]] = {
    "issue_payout": re.compile(
        r"\b(?:i|we)(?:'ve| have)?\s+(?:now\s+)?(?:paid|issued|released|sent)\b[^.?!]{0,30}"
        r"\b(?:payment|payout|money|amount)\b"
        r"|\b(?:the|your|that|this)\s+(?:payment|payout)\b[^.?!]{0,50}"
        r"\b(?:has been|was|is now)\s+(?:issued|paid|made|released|sent)\b",
        re.I,
    ),
    "withdraw_claim": re.compile(
        r"\b(?:i|we)(?:'ve| have)\s+(?:now\s+)?withdrawn\b"
        r"|\b(?:claim|it)\b[^.?!]{0,20}\b(?:has been|is now)\s+withdrawn\b",
        re.I,
    ),
    "register_claim": re.compile(
        r"\b(?:i|we)(?:'ve| have)\s+(?:now\s+)?registered\b"
        r"|\bclaim\b[^.?!]{0,30}\b(?:has been|is now)\s+registered\b",
        re.I,
    ),
    "submit_document": re.compile(
        r"\b(?:is now|has been)\s+(?:attached|added)\b"
        r"|\b(?:i|we)(?:'ve| have)\s+(?:now\s+)?(?:attached|added)\b",
        re.I,
    ),
}
"""How a claim that each effect happened looks in prose — one per write the
claims system offers, held complete by a test against the AOAS's operations.
Each needs an affirmative construction, never the bare verb (F-004): "I couldn't
withdraw it" claims nothing."""


def _succeeded(result: ToolResult, action: str) -> bool:
    if result.name != action or result.is_error:
        return False
    structured = result.structured
    return not (isinstance(structured, dict) and structured.get("allowed") is False)


def no_unclaimed_effect(ctx: Context) -> Verdict:
    """An effect may be *claimed* only if it happened this turn."""
    for action, pattern in CLAIM_PATTERNS.items():
        if pattern.search(ctx.text) and not any(_succeeded(r, action) for r in ctx.tool_results):
            return block("no_unclaimed_effect", f"claimed {action} happened when it did not")
    return ALLOW


PROMISE = re.compile(
    r"\bwill\s+(?:definitely\s+|surely\s+|certainly\s+|probably\s+)?be\s+"
    r"(?:approved|covered|paid|accepted|reimbursed|settled)\b"
    r"|\byou(?:'ll|\s+will)\s+(?:definitely\s+|surely\s+|certainly\s+|probably\s+)?"
    r"(?:get|receive|be\s+(?:paid|reimbursed|covered|compensated))\b"
    r"|\b(?:is|are)\s+(?:fully\s+)?covered\b|\bfully\s+covered\b",
    re.I,
)
"""A reply that sounds like a decision about cover or money (P-NO-PROMISE)."""
_EXPLAINING = re.compile(r"\b(?:whether|cannot|can't|can not|unable|only\s+the)\b", re.I)


def no_promise_of_cover(ctx: Context) -> Verdict:
    """P-NO-PROMISE, R-COVERAGE at the output: never say or imply a claim is
    covered, will be approved or paid, or for what amount. A sentence explaining
    that only the assessment decides ("I cannot say whether it is covered") is
    not a promise and is let through (F-004)."""
    for sentence in re.split(r"(?<=[.!?])\s+", ctx.text):
        if PROMISE.search(sentence) and not _EXPLAINING.search(sentence):
            return block("no_promise_of_cover", f"promised an outcome: {sentence[:80]!r}")
    return ALLOW


IDENTIFIER = re.compile(r"\b[A-Z]{1,4}-\d{3,10}\b")
MONEY = re.compile(r"(?:(?:Rs\.?|INR|₹)\s*)([\d,]+(?:\.\d{2})?)|\b(\d[\d,]{2,})\b")
ISO_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")


def _flat(text: str) -> str:
    return re.sub(r"[,\s]", "", text)


def no_ungrounded_entity(ctx: Context) -> Verdict:
    """Every reference, amount and date in the reply appears in what the tools
    returned this turn — so no figure nobody decided reaches the policyholder."""
    if not ctx.tool_results:
        return ALLOW
    evidence = " ".join(f"{r.structured} {r.text}" for r in ctx.tool_results).lower()
    for found in set(IDENTIFIER.findall(ctx.text)):
        if found.lower() not in evidence:
            return block("no_ungrounded_entity", f"cited {found!r}, which no tool returned")
    for date in ISO_DATE.findall(ctx.text):
        if date not in evidence:
            return block("no_ungrounded_entity", f"stated the date {date!r}, unsupported")
    flat = _flat(evidence)
    for match in MONEY.finditer(IDENTIFIER.sub(" ", ctx.text)):
        amount = match.group(1) or match.group(2)
        if amount and (_flat(amount).lstrip("0") or "0") not in flat:
            return block("no_ungrounded_entity", f"stated the figure {amount!r}, unsupported")
    return ALLOW


_CLAUSES = re.compile(r",\s*(?:and|but)\s+|;\s*|\s+(?:and|but)\s+(?=yes\b|you\b|i\b)", re.I)


def no_superseded_claim_state(ctx: Context) -> Verdict:
    """The harness's `no_superseded_state`, judged clause by clause.

    The harness skips a whole sentence that holds a modal anywhere in it, so
    "your claim is still under assessment, and yes, you can send the photo"
    escaped on the "can" of its second clause while its first contradicted the
    latest read (FINDINGS F-4). Split first, the first clause is judged alone.
    """
    for clause in _CLAUSES.split(ctx.text):
        verdict = no_superseded_state(replace(ctx, text=clause))
        if verdict.blocked:
            return verdict
    return ALLOW


DIGITS = re.compile(r"\b\d{9,18}\b")
LICENCE = re.compile(r"\b[A-Z]{2}[- ]?\d{2}[- ]?\d{4}[- ]?\d{7}\b")


def no_pii_echo(ctx: Context) -> Verdict:
    """Never read a bank account or driving licence number back (AOAS
    `personal_data_in_conversation`): it puts it in a transcript, a log and a
    screenshot."""
    if DIGITS.search(ctx.text) or LICENCE.search(ctx.text):
        return block("no_pii_echo", "an account- or licence-shaped number appeared in the reply")
    return ALLOW


OUTPUT_RULES: tuple[Rule, ...] = (
    no_unclaimed_effect,
    no_promise_of_cover,
    no_ungrounded_entity,
    no_superseded_state,
    no_superseded_claim_state,
    no_pii_echo,
)

REPLY_RULES: tuple[Rule, ...] = (no_promise_of_cover, no_pii_echo)
"""The rules that judge a reply on its own text, on every route — the
deterministic templates included."""

CONSENTED_TOOLS = frozenset(
    {"withdraw_claim", "request_payout", "register_claim", "submit_document"}
)
"""AOAS P-OWN-WORDS `via`: the actions a policyholder's own words authorise."""

ANY_POLICY = "*"
"""A report naming no policy reference: policyholders name their car, not their
policy number, and nothing on the agent's side can resolve a registration to a
policy before the call (FINDINGS F-7). The claims system still holds the call to
the caller's own active policy (P-OWNERSHIP, P-FNOL)."""


def policyholder_asked(ctx: Context) -> Verdict:
    """P-OWN-WORDS, AHC-0116: an action runs only on a record the policyholder's
    own words asked for it on — read from what they said, never from a tool's
    result, so an instruction planted in a claim note moves nothing."""
    if ctx.position is not Position.PRE_TOOL or ctx.tool_name not in CONSENTED_TOOLS:
        return ALLOW
    record = ctx.arguments.get("id") or ctx.arguments.get("claim_id")
    consented = ctx.identity.consented
    if f"{ctx.tool_name}:{record}" in consented or f"{ctx.tool_name}:{ANY_POLICY}" in consented:
        return ALLOW
    action = ctx.tool_name.replace("_", " ")
    return block(
        "policyholder-asked",
        f"the policyholder has not asked for {action} on {record}. Ask them whether they"
        " want it, and do not say it has been done",
    )


DEFAULT_RULES: dict[Position, tuple[Rule, ...]] = {
    Position.PRE_MODEL: (),
    Position.POST_MODEL: OUTPUT_RULES,
    Position.PRE_TOOL: (policyholder_asked,),
    Position.POST_TOOL: (),
    Position.REPLY: REPLY_RULES,
}

use_default_rules(DEFAULT_RULES)

__all__ = [
    "ANY_POLICY",
    "CLAIM_PATTERNS",
    "CONSENTED_TOOLS",
    "DEFAULT_RULES",
    "OUTPUT_RULES",
    "REPLY_RULES",
    "SAFE_REPLY",
    "Context",
    "Position",
    "Rule",
    "Verdict",
    "block",
    "enforce",
    "no_pii_echo",
    "no_promise_of_cover",
    "no_superseded_claim_state",
    "no_superseded_state",
    "no_unclaimed_effect",
    "no_ungrounded_entity",
    "policyholder_asked",
]
