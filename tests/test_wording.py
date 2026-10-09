"""A policyholder never reads another domain's words (FINDINGS F-14).

When the claims system cannot be reached, the direct route and the loop both
say so in this insurer's words. Until agent_harness 205b04a the loop said "our
order system", because the sentence was the clothing agent's, held as a library
constant.
"""

from __future__ import annotations

import pytest
from agent_harness.llm import ScriptedClient
from agent_harness.state import InMemoryCheckpointStore
from kit import me

from claims_fnol import binding
from claims_fnol import entrypoint as ep
from claims_fnol.contracts import Failed, Identity, ToolUnavailable


class Down:
    """A claims system that cannot be reached at all."""

    async def list_tools(self, identity: Identity) -> object:
        raise ToolUnavailable("connection refused")

    async def call(self, *args: object, **kwargs: object) -> object:
        raise ToolUnavailable("connection refused")


ROUTES = [
    # (route, what the policyholder says)
    ("direct", "What's the status of claim CLM-010001?"),
    ("loop", "Someone smashed my windscreen last night"),
]


@pytest.mark.parametrize(("route", "text"), ROUTES, ids=[r[0] for r in ROUTES])
async def test_an_unreachable_claims_system_is_named_in_the_insurers_words(
    route: str, text: str
) -> None:
    agent = ep.build(llm=ScriptedClient([]), tools=Down(), store=InMemoryCheckpointStore())  # type: ignore[arg-type]
    result, _ = await agent.handle(text, identity=me())
    assert isinstance(result, Failed), route
    assert result.customer_message == binding.UNREACHABLE
    assert "order" not in result.customer_message.lower()
