"""Tier 2b — every environment is an overlay; no code asks which one it is.

1. Each overlay resolves against the harness profile, through the library's
   registry, with every adapter it names existing — Azure's included, which
   cannot run until Tier 3 has made its resources.
2. The Azure overlay is the profile: every port bound as the stack binds it, so
   nothing in it says `why`. The others say why wherever they leave it.
3. No module of the composition root branches on the environment's name or a
   vendor's: `CLAIMS_FNOL_ENV` is read in one place, to pick the file.
4. The claims system is composed the same way from its own overlays
   (`config/claims-system/`, A1), `CLAIMS_SYSTEM_ENV` read in one place; its
   overlays are held to their adapters in `tests/test_claims_edge.py`.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
from agent_harness import adapters

from claims_fnol_app.compose import CONFIG, overlay

APP = Path(__file__).resolve().parents[1] / "src" / "claims_fnol_app"

# (overlay, the adapter it binds per port)
OVERLAYS: list[tuple[str, dict[str, str]]] = [
    (
        "local",
        {
            "secrets": "environment-settings",
            "telemetry": "console",
            "model": "groq-direct",
            "state": "postgres",
            "records": "postgres",
            "identity": "local-dev",
            "tool_runtime": "mcp-client",
            "approval": "dbos-workflows",
        },
    ),
    (
        "test",
        {
            "secrets": "environment-settings",
            "telemetry": "console",
            "model": "scripted",
            "state": "postgres",
            "records": "postgres",
            "identity": "local-dev",
            "tool_runtime": "mcp-client",
            "approval": "dbos-workflows",
        },
    ),
    (
        "azure",
        {
            "secrets": "key-vault",
            "telemetry": "azure-monitor-otel",
            "model": "apim-ai-gateway",
            "state": "azure-postgresql-flexible",
            "records": "postgres",
            "identity": "entra-id",
            "tool_runtime": "mcp-client",
            "approval": "dbos-workflows",
        },
    ),
]


@pytest.mark.discharges("AHC-0004")
@pytest.mark.parametrize(("name", "expected"), OVERLAYS, ids=[o[0] for o in OVERLAYS])
def test_each_overlay_resolves_to_existing_adapters(name: str, expected: dict[str, str]) -> None:
    planned = adapters.plan(overlay(name))
    assert planned.environment == name
    assert {port: planned.adapter(port) for port in planned.bound} == expected
    assert set(planned.bound) == set(adapters.PORTS), "every composed port is bound"


@pytest.mark.discharges("AHC-0004")
@pytest.mark.parametrize(("name", "expected"), OVERLAYS, ids=[o[0] for o in OVERLAYS])
def test_an_overlay_leaves_the_profile_only_saying_why(name: str, expected: dict[str, str]) -> None:
    planned = adapters.plan(overlay(name))
    stack = planned.profile["bindings"]
    for port, bound in planned.bound.items():
        left = stack.get(port, {}).get("adapter") not in (None, bound.adapter.name)
        assert bool(bound.why) is left, f"{name}.{port}: why={bound.why!r}, left={left}"
    if name == "azure":
        assert not any(b.why for b in planned.bound.values()), "Azure is the profile itself"


def test_the_overlays_are_the_three_environments() -> None:
    assert sorted(p.stem for p in CONFIG.glob("*.yaml")) == ["azure", "local", "test"]
    with pytest.raises(SystemExit, match="overlays: azure, local, test"):
        overlay("staging")


BRANCH = re.compile(r"""==\s*["'](?:local|test|azure|groq|openai|entra|dbos|temporal)["']""")


@pytest.mark.discharges("AHC-0004")
@pytest.mark.parametrize("module", sorted(p.name for p in APP.glob("*.py")))
def test_no_module_branches_on_an_environment_or_a_vendor(module: str) -> None:
    source = (APP / module).read_text()
    assert not BRANCH.search(source), f"{module} compares against an environment or vendor name"
    reads = [
        node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Constant) and node.value == "CLAIMS_FNOL_ENV"
    ]
    assert len(reads) == (1 if module == "compose.py" else 0), "only overlay() reads it"


CLAIMS = Path(__file__).resolve().parents[1] / "src" / "claims_system"


@pytest.mark.discharges("AHC-0004")
@pytest.mark.parametrize("module", sorted(p.name for p in CLAIMS.glob("*.py")))
def test_no_claims_system_module_branches_on_an_environment_or_a_vendor(module: str) -> None:
    source = (CLAIMS / module).read_text()
    assert not BRANCH.search(source), f"{module} compares against an environment or vendor name"
    reads = [
        node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Constant) and node.value == "CLAIMS_SYSTEM_ENV"
    ]
    assert len(reads) == (1 if module == "__main__.py" else 0), "only overlay() reads it"
