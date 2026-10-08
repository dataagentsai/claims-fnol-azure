"""This agent, as a runner that has never seen it drives it — `agenttwin.Binding`.

    uv run python -m agenttwin run --binding evals.gate_binding:open_subject <scenarios>

The model is the provider twin, reached over HTTP through the harness's
OpenAI-compatible adapter and wrapped in `ResilientLLM` as the deployment wraps
it. Named in `gates.yaml`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

from agent_harness.llm import GroqClient
from agenttwin import Clock, Live, ModelEndpoint, Subject

from evals.simulation import subject_for


@asynccontextmanager
async def open_subject(
    live: Live, *, wrap: Callable[..., Any], clock: Clock, model: ModelEndpoint
) -> AsyncIterator[Subject]:
    llm = GroqClient(
        api_key=model.api_key, base_url=model.base_url, model=model.model, provider="agenttwin"
    )
    async with subject_for(live, llm=llm, clock=clock, wrap=wrap) as subject:
        yield subject


__all__ = ["open_subject"]
