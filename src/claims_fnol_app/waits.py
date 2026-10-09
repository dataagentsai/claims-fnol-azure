"""The payout worker's login: the one part of the durable waits that is this agent's.

The waits themselves — approvals and escalations, on DBOS here and on Temporal
in the gates — are the library's adapters, chosen by the overlay's `approval`
binding (`agent_harness.adapters.waits`). What runs inside a payout's wait is
this agent's `PayoutWork`, handed to the adapter as the hook `work`
(`compose.hooks`), and it runs under this login.
"""

from __future__ import annotations

from claims_fnol.binding import POLICYHOLDER_SCOPES
from claims_fnol.contracts import Identity


async def acting_for(policyholder_id: str) -> Identity:
    """The payout worker's login: the policyholder's ordinary scopes, which only
    a granted approval elevates (`granted_identity`), never this login."""
    return Identity(customer_id=policyholder_id, scopes=POLICYHOLDER_SCOPES)


__all__ = ["acting_for"]
