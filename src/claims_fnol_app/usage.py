"""What the model was asked, counted: local telemetry you can read in a terminal.

Locally the traces stay in the harness's in-memory exporter (App Insights is
Tier 4), so this prints one line per model call — model, tokens, seconds — and
keeps running totals that `GET /dev/usage` returns in the local environment.
The smoke check reads those totals before and after each flow.
"""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass, field
from typing import TextIO

from agent_harness.state import Conversation
from agent_harness.telemetry import redact

from claims_fnol import entrypoint as ep
from claims_fnol.contracts import (
    CheckpointStore,
    Escalations,
    Identity,
    LLMClient,
    ModelRequest,
    ModelResponse,
    TurnResult,
)


@dataclass
class Counted:
    """An `LLMClient` that counts what passes through it. Outermost, so a retry
    inside `ResilientLLM` shows as one call that took longer."""

    inner: LLMClient
    out: TextIO = field(default=sys.stdout, repr=False)
    calls: int = 0
    failures: int = 0
    input_tokens: int = 0
    output_tokens: int = 0

    async def complete(self, request: ModelRequest) -> ModelResponse:
        started = time.monotonic()
        try:
            response = await self.inner.complete(request)
        except Exception as exc:
            self.failures += 1
            print(f"  model  FAILED {type(exc).__name__}: {str(exc)[:160]}", file=self.out)
            raise
        self.calls += 1
        self.input_tokens += response.usage.input_tokens
        self.output_tokens += response.usage.output_tokens
        took = time.monotonic() - started
        tools = ",".join(c.name for c in response.tool_calls) or "-"
        said = redact(response.text or "")[:240].replace("\n", " ")
        print(
            f"  model  {response.model or '?'}  in={response.usage.input_tokens} "
            f"out={response.usage.output_tokens}  {took:.1f}s  tools={tools}"
            + (f"  says={said!r}" if said else ""),
            file=self.out,
            flush=True,
        )
        return response

    def totals(self) -> dict[str, int]:
        return {
            "calls": self.calls,
            "failures": self.failures,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
        }


@dataclass
class Logged:
    """The agent, with one console line per turn: its outcome and, for a refusal,
    the rule that refused it — what a trace backend shows, on a Mac with none."""

    agent: ep.Agent
    out: TextIO = field(default=sys.stdout, repr=False)

    @property
    def store(self) -> CheckpointStore:
        return self.agent.store

    @property
    def escalations(self) -> Escalations | None:
        return self.agent.escalations

    async def opening(self, identity: Identity) -> str:
        return await self.agent.opening(identity)

    async def handle(
        self, text: str, *, identity: Identity, **rest: object
    ) -> tuple[TurnResult, Conversation]:
        result, conversation = await self.agent.handle(text, identity=identity, **rest)  # type: ignore[arg-type]
        why = getattr(result, "rule_id", "") or getattr(result, "detail", "") or ""
        reason = getattr(result, "reason", "") or ""
        print(
            f"  turn   {identity.customer_id}  {type(result).__name__}"
            + (f"  rule={why} reason={redact(str(reason))[:200]!r}" if why or reason else ""),
            file=self.out,
            flush=True,
        )
        return result, conversation


__all__ = ["Counted", "Logged"]
