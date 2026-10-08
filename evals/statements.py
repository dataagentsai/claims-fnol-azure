"""Every statement a test may claim to discharge, from the specs that hold one.

| Shape | Spec | Source |
|---|---|---|
| `AAC-0106` | assurance catalog | the sibling `ai-assurance-catalog/catalog` |
| `AHC-0057` | harness catalog | the sibling `ai-harness-catalog/capabilities` |
| `P-PAYOUT`, `R-COVERAGE`, `Q-STEPS` | this agent's AOAS — policies, refusals, properties | its spec |
| `op:…`, `esc:…`, `ext:…`, `fact:…` | its operations, escalation rules, externals, facts | its spec |

An unknown id fails collection: a tag that names nothing reads as coverage and
verifies nothing.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
FAMILY = ROOT.parent
AOAS = FAMILY / "clean-ai-engineering" / "drafts" / "examples" / "motor-claims-fnol.aoas.yaml"
AHC = FAMILY / "ai-harness-catalog" / "capabilities"
AAC = FAMILY / "ai-assurance-catalog" / "catalog"


def aoas() -> dict[str, object]:
    loaded: dict[str, object] = yaml.safe_load(AOAS.read_text())
    return loaded


def aoas_ids(spec: dict | None = None) -> set[str]:
    spec = spec or aoas()
    ids: set[str] = {r["id"] for r in spec["purpose"]["refuses"]}
    for key, value in spec["policies"].items():
        if key.startswith("P-"):
            ids.add(key)
    ids |= {s["id"] for s in spec["policies"]["approval"]["statements"]}
    escalation = spec["policies"]["escalation"]
    ids |= {s["id"] for s in escalation["statements"]}
    for tier in ("on_request", "on_reply", "on_condition"):
        ids |= {f"esc:{r['id']}" for r in escalation.get(tier) or []}
    ids |= {f"op:{name}" for name in spec["operations"]}
    ids |= {f"ext:{name}" for name in spec["external"]}
    ids |= {f"fact:{name}" for name in spec["facts"]}
    ids |= {p["id"] for p in spec["required"]["properties"]}
    return ids


def catalog_ids() -> set[str]:
    found: set[str] = set()
    for folder in (AHC, AAC):
        for path in folder.glob("*.yaml"):
            entry = yaml.safe_load(path.read_text())
            if isinstance(entry, dict) and "id" in entry:
                found.add(entry["id"])
    return found


@dataclass(frozen=True)
class Vocabulary:
    known: frozenset[str]

    def unknown(self, ids: tuple[str, ...]) -> list[str]:
        return [i for i in ids if i not in self.known]


def load() -> Vocabulary:
    return Vocabulary(known=frozenset(aoas_ids() | catalog_ids()))


__all__ = ["AOAS", "Vocabulary", "aoas", "aoas_ids", "catalog_ids", "load"]
