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

from claims_fnol.contracts import LLMClient, ModelRequest, ModelResponse


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
        print(
            f"  model  {response.model or '?'}  in={response.usage.input_tokens} "
            f"out={response.usage.output_tokens}  {took:.1f}s  tools={tools}",
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


__all__ = ["Counted"]
