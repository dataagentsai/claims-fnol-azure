"""Which version is running (L15): this agent's settings, resolved once into a fingerprinted record.

The ceilings every loop is bounded by (`Budgets`) are the harness's; which
models this agent approves, what its settings are called, and what goes into its
fingerprint are its own.
"""

from __future__ import annotations

import hashlib
import json
from typing import Literal

from agent_harness.config import Budgets
from agent_harness.contracts.failures import AgentFailure, Fault
from pydantic import BaseModel, ConfigDict, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ResolutionMode = Literal["mock", "replay", "real", "shadow"]


class UnapprovedModel(AgentFailure):
    """AAC-0094 / Q-MODEL — only approved models are reachable, checked at startup."""

    fault = Fault.MISCONFIGURED


class RulesMismatch(AgentFailure):
    """The routing label in the fingerprint is not the rules the agent loaded."""

    fault = Fault.MISCONFIGURED


class Settings(BaseSettings):
    """Environment-driven inputs, read only by the composition root."""

    model_config = SettingsConfigDict(env_prefix="CLAIMS_", env_file=".env", extra="ignore")

    model: str = "openai/gpt-oss-120b"
    approved_models: tuple[str, ...] = ("openai/gpt-oss-120b", "openai/gpt-oss-20b")
    """Q-MODEL: the approved list. The pin is `harness-profile.yaml`'s
    `bindings.model.x_model.id`, and a test holds the two together."""
    provider: str = "groq"
    provider_base_url: str = "https://api.groq.com/openai/v1"
    provider_api_key: str = Field(default="", repr=False)
    mcp_base_url: str = "http://localhost:9050/mcp"
    prompt_version: str = "v1"
    router_rules_version: str = "v1"
    resolution: ResolutionMode = "real"
    sealed: bool = False
    temperature: float = 0.0
    max_steps: int = 12
    """Q-STEPS."""
    max_cost_usd: float = 0.50
    """Q-COST."""
    max_output_tokens: int = 4096
    """Q-OUTPUT."""
    max_tool_result_chars: int = 8000
    """Q-TOOL-RESULT."""
    retention_days: int = 30
    """Q-RETENTION."""


class RunConfig(BaseModel):
    """The resolved, frozen configuration for one run, plus its fingerprint."""

    model_config = ConfigDict(frozen=True)

    model: str
    provider: str
    provider_base_url: str
    mcp_base_url: str
    prompt_version: str
    router_rules_version: str
    resolution: ResolutionMode
    sealed: bool
    temperature: float
    budgets: Budgets

    @property
    def fingerprint(self) -> str:
        """A stable hash over everything that changes behaviour; endpoints and
        secrets are routes, not systems, and are left out."""
        material = self.model_dump(mode="json", exclude={"mcp_base_url", "provider_base_url"})
        canonical = json.dumps(material, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def resolve(settings: Settings) -> RunConfig:
    """Validate and freeze. Fails at startup, never mid-conversation."""
    if settings.model not in settings.approved_models:
        raise UnapprovedModel(
            f"{settings.model!r} is not in the approved list {settings.approved_models}"
        )
    if settings.sealed and settings.resolution in ("real", "shadow"):
        raise ValueError(f"sealed run cannot use resolution={settings.resolution!r}")
    return RunConfig(
        model=settings.model,
        provider=settings.provider,
        provider_base_url=settings.provider_base_url,
        mcp_base_url=settings.mcp_base_url,
        prompt_version=settings.prompt_version,
        router_rules_version=settings.router_rules_version,
        resolution=settings.resolution,
        sealed=settings.sealed,
        temperature=settings.temperature,
        budgets=Budgets(
            max_steps=settings.max_steps,
            max_cost_usd=settings.max_cost_usd,
            max_output_tokens=settings.max_output_tokens,
            max_tool_result_chars=settings.max_tool_result_chars,
        ),
    )


__all__ = [
    "Budgets",
    "ResolutionMode",
    "RulesMismatch",
    "RunConfig",
    "Settings",
    "UnapprovedModel",
    "resolve",
]
