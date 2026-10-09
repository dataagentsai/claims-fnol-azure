"""The model's typography, made plain before anything reads it.

The pinned model (gpt-oss-120b on Groq) writes references with a non-breaking
hyphen (U+2011: "CLM‑019002") and puts narrow no-break spaces (U+202F) between
words. Every reader of a reply and of tool arguments — the output rules, the
created-reference check, the claims system's key lookup — matches the ASCII
hyphen, so a correct reply read as citing nothing, and an argument named no
claim (FINDINGS F-18). Normalised here, at the one place a model answer enters
the agent, rather than in each pattern.
"""

from __future__ import annotations

from dataclasses import dataclass

from claims_fnol.contracts import LLMClient, ModelRequest, ModelResponse, ToolCall

TYPOGRAPHY = str.maketrans(
    {
        "‐": "-",  # hyphen
        "‑": "-",  # non-breaking hyphen
        "‒": "-",  # figure dash
        " ": " ",  # no-break space
        " ": " ",  # figure space
        " ": " ",  # narrow no-break space
        "⁠": "",  # word joiner
        "​": "",  # zero-width space
    }
)
"""Only characters that stand for an ASCII hyphen or space, or for nothing. An
en or em dash is punctuation in prose and is left alone."""


def plain(text: str) -> str:
    return text.translate(TYPOGRAPHY)


def _call(call: ToolCall) -> ToolCall:
    arguments = {k: plain(v) if isinstance(v, str) else v for k, v in call.arguments.items()}
    return call.model_copy(update={"arguments": arguments})


@dataclass(frozen=True)
class PlainText:
    """An `LLMClient` whose answers carry plain hyphens and spaces."""

    inner: LLMClient

    async def complete(self, request: ModelRequest) -> ModelResponse:
        response = await self.inner.complete(request)
        return response.model_copy(
            update={
                "text": plain(response.text or ""),
                "tool_calls": tuple(_call(c) for c in response.tool_calls),
            }
        )


__all__ = ["TYPOGRAPHY", "PlainText", "plain"]
