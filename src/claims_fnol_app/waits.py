"""The durable waits on DBOS: payout approvals and escalations (Tier 2).

The Azure stack binds `approval` and `workflow` to DBOS on the PostgreSQL it
already runs (`stacks/azure.yaml`); the adapters are the harness's
(`agent_harness.approvals.dbos`, `agent_harness.escalation.dbos`,
`agent_harness.state.dbos`). What is this agent's is the work a payout's wait
runs as its steps — `PayoutWork.assess` and `.carry_out`, the same callables the
Temporal worker registers in the gates — and the login it runs them under.

One DBOS per process, launched after both wait modules are imported (DBOS
registers workflows at import and recovers the ones still waiting at launch).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from agent_harness.approvals import dbos as approvals_dbos
from agent_harness.escalation import dbos as escalation_dbos
from agent_harness.state import dbos as box

from claims_fnol.approvals import PayoutWork, Policy
from claims_fnol.binding import POLICYHOLDER_SCOPES
from claims_fnol.contracts import Identity, ToolClient


@dataclass(frozen=True)
class Waits:
    """The agent's handles (request, raise, read) and the desk's (decide, close)."""

    approvals: approvals_dbos.DBOSApprovals
    escalations: escalation_dbos.DBOSEscalations
    approver: approvals_dbos.DBOSApprovalDesk
    desk: escalation_dbos.DBOSEscalationDesk


async def acting_for(policyholder_id: str) -> Identity:
    """The payout worker's login: the policyholder's ordinary scopes, which only
    a granted approval elevates (`granted_identity`), never this login."""
    return Identity(customer_id=policyholder_id, scopes=POLICYHOLDER_SCOPES)


@asynccontextmanager
async def running(database_url: str, tools: ToolClient) -> AsyncIterator[Waits]:
    """DBOS on `database_url`, serving the payout's steps over `tools` — the
    worker's own connection to the claims system — until the block exits."""
    policy = Policy()
    work = PayoutWork(tools, acting_for=acting_for, policy=policy)
    approvals_dbos.serve(approvals_dbos.Work(assess=work.assess, carry_out=work.carry_out))
    box.launch(database_url, name="claims-fnol")
    try:
        yield Waits(
            approvals=approvals_dbos.DBOSApprovals(policy=policy),
            escalations=escalation_dbos.DBOSEscalations(),
            approver=approvals_dbos.DBOSApprovalDesk(),
            desk=escalation_dbos.DBOSEscalationDesk(),
        )
    finally:
        box.shutdown()


__all__ = ["Waits", "acting_for", "running"]
