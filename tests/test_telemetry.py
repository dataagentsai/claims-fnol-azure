"""What a turn records: spans that meet the contract, the route and its reason, every policy decision, and no personal data.

The tracer, the span contract and the redaction point are the harness's; the
names (`claims_fnol`, `claims-fnol`), the spans this agent opens and the AOAS's
personal-data patterns are this agent's.
"""

from __future__ import annotations

import pytest
from agent_harness import telemetry as tel
from agent_harness.telemetry.contract import validate
from agent_harness.telemetry.redaction import redact
from kit import agent, me, says

from claims_fnol.telemetry import SCOPE, SERVICE


def spans_of(exporter: object) -> list[object]:
    return list(exporter.get_finished_spans())  # type: ignore[attr-defined]


# [words, the route recorded, the answers]
TURNS = [
    ("Will my claim be approved?", "refuse", []),
    ("Where is my claim CLM-010006?", "direct", []),
    ("hello, a question about my policies", "agentic", [says("Hello — I can help.")]),
]


@pytest.mark.discharges("AHC-0027", "AHC-0114", "AHC-0018", "AHC-0010")
@pytest.mark.parametrize(("words", "kind", "answers"), TURNS, ids=[t[1] for t in TURNS])
async def test_every_turn_records_its_route_and_meets_the_span_contract(
    words: str, kind: str, answers: list[object]
) -> None:
    exporter = tel.configure()
    async with agent(answers) as (built, _):
        await built.handle(words, identity=me())
    spans = spans_of(exporter)
    assert validate(spans) == []  # type: ignore[arg-type]
    route = next(s for s in spans if s.name == "agent.route")  # type: ignore[attr-defined]
    assert route.attributes[tel.ROUTE_KIND] == kind  # type: ignore[attr-defined]
    assert route.attributes[tel.ROUTE_REASON]  # type: ignore[attr-defined]
    ours = {s.instrumentation_scope.name for s in spans if s.name.startswith("agent.")}  # type: ignore[attr-defined]
    assert ours == {SCOPE}
    turn = next(s for s in spans if s.name == "agent.turn")  # type: ignore[attr-defined]
    assert turn.resource.attributes["service.name"] == SERVICE  # type: ignore[attr-defined]
    policy = [s for s in spans if s.name == "agent.policy"]  # type: ignore[attr-defined]
    assert policy, "every policy position that ran left a record, an allow included"


@pytest.mark.discharges("AHC-0018", "AHC-0094")
async def test_a_blocked_reply_records_the_rule_that_blocked_it() -> None:
    exporter = tel.configure()
    async with agent([says("It will be approved and you'll get ₹40,000.")]) as (built, _):
        result, _ = await built.handle("hello, a question about my policies", identity=me())
    blocked = [
        s.attributes.get("agent.policy.blocked_by")  # type: ignore[attr-defined]
        for s in spans_of(exporter)
        if s.name == "agent.policy"  # type: ignore[attr-defined]
    ]
    assert "no_promise_of_cover" in blocked
    assert "40,000" not in getattr(result, "reply", "")


# [what was typed, what must not be exported]
PERSONAL = [
    ("my account number is 50100234567890", "50100234567890"),
    ("licence KA0120231234567", "KA0120231234567"),
    ("call me on 9845031207", "9845031207"),
    ("rohan.iyer@example.test", "rohan.iyer@example.test"),
]


@pytest.mark.discharges("AHC-0019")
@pytest.mark.parametrize(("typed", "secret"), PERSONAL, ids=[p[1] for p in PERSONAL])
async def test_personal_data_is_redacted_before_it_leaves_the_process(
    typed: str, secret: str
) -> None:
    assert secret not in redact(typed)
    exporter = tel.configure(capture_payloads=True, capture_sample=1.0)
    async with agent([says("Thank you.")]) as (built, _):
        await built.handle(f"hello, {typed}", identity=me())
    exported = " ".join(str(dict(s.attributes or {})) for s in spans_of(exporter))  # type: ignore[attr-defined]
    assert secret not in exported
