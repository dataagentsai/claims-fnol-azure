"""Typed boundaries — the harness's contracts, and this insurer's vocabulary beside them.

Importing this package declares both to the harness: `Intent` as the intents
every route validates against, and the reference shapes and status grammar of
`contracts.reading` as the vocabulary its checks read.
"""

from agent_harness.contracts import *  # noqa: F403 — the harness's contracts, re-exported whole
from agent_harness.contracts import __all__ as _HARNESS
from agent_harness.contracts import use_intents
from agent_harness.contracts.reading import use_vocabulary

from claims_fnol.contracts import reading
from claims_fnol.contracts.domain import ClaimStatus, Intent, PolicyStatus

use_intents(Intent)
use_vocabulary(reading.VOCABULARY)

__all__ = [*_HARNESS, "ClaimStatus", "Intent", "PolicyStatus"]  # noqa: PLE0604
