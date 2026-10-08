"""P-FNOL's reference: a claim registered this turn is named, whatever the model said.

The AOAS says a new claim's policyholder "is given the claim reference" — an
obligation on the turn, not a hope about the model's wording. The claims system
returns the reference it made (`created`, AOAS `register_claim.creates`), and
the harness's loop keeps only the row each write acted on, so the reference
would be lost between the tool and the reply (FINDINGS F-8). This per-turn view
of the tool client keeps it, and `given` names it when the reply did not.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from claims_fnol.contracts import (
    Completed,
    IdempotencyKey,
    Identity,
    ToolClient,
    ToolRegistry,
    ToolResult,
    TurnResult,
)

GIVEN = "Your claim reference is {refs}."


@dataclass
class Created:
    """The tool client, for one turn, remembering every reference it created."""

    inner: ToolClient
    made: list[str] = field(default_factory=list)

    async def list_tools(self, identity: Identity) -> ToolRegistry:
        return await self.inner.list_tools(identity)

    async def call(
        self,
        name: str,
        arguments: dict[str, object],
        identity: Identity,
        idempotency_key: IdempotencyKey,
    ) -> ToolResult:
        result = await self.inner.call(name, arguments, identity, idempotency_key)
        said = result.structured if isinstance(result.structured, dict) else {}
        created = said.get("created")
        if not result.is_error and said.get("allowed") is not False and isinstance(created, dict):
            ref = created.get("id")
            if isinstance(ref, str) and ref and ref not in self.made:
                self.made.append(ref)
        return result


def given(result: TurnResult, made: list[str]) -> TurnResult:
    """The turn's result, naming every reference created and not yet named."""
    if not isinstance(result, Completed):
        return result
    missing = [ref for ref in made if ref not in result.reply]
    if not missing:
        return result
    named = GIVEN.format(refs=", ".join(missing))
    return result.model_copy(update={"reply": f"{result.reply} {named}".strip()})


__all__ = ["GIVEN", "Created", "given"]
