"""The discharges plugin: what each test verifies, on its JUnit record.

    @pytest.mark.discharges("P-PAYOUT", "AHC-0057")
    @pytest.mark.unwired   # tests a component the agent never calls — counts for nothing

The gates (`clean-ai-engineering/tools/gates.py`) read `discharges` off the
JUnit report; an id that names nothing in the AOAS or the catalogs fails
collection before anything runs.
"""

from __future__ import annotations

import pytest
from evals import statements

_vocab: statements.Vocabulary | None = None


def pytest_configure(config: pytest.Config) -> None:
    global _vocab
    _vocab = statements.load()


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    bad: dict[str, list[str]] = {}
    for item in items:
        marker = item.get_closest_marker("discharges")
        ids: list[str] = []
        for mark in item.iter_markers("discharges"):
            ids.extend(mark.args)
        if marker is not None:
            item.user_properties.append(("discharges", ",".join(dict.fromkeys(ids))))
        if item.get_closest_marker("unwired") is not None:
            item.user_properties.append(("unwired", "true"))
        if _vocab is not None and (unknown := _vocab.unknown(tuple(ids))):
            bad[item.nodeid.split("[", 1)[0]] = unknown
    if bad:
        detail = "\n".join(f"  {test}: {', '.join(ids)}" for test, ids in sorted(bad.items()))
        raise pytest.UsageError(f"discharges names statements that do not exist:\n{detail}")
