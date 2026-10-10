-- Our own approval and escalation records (Tier 4a, A3), in the agent's
-- database beside `agent_state.checkpoints` and DBOS's system tables.
--
-- The approval wait's record used to be a DBOS workflow event, and the claims
-- system read it from DBOS's internal tables to check a payout: the money path
-- depended on DBOS's storage format. These rows are ours. The waits write them
-- through the harness's `records` port (agent_harness.state.records.PostgresRecords,
-- an upsert keyed by the wait's workflow id), in the step that moves their
-- state; the claims system reads `agent_state.approvals` through the same port
-- on its own, read-only connection (CLAIMS_RECORDS_DATABASE_URL).
--
-- As the reference agent's sql/002_records.sql declares them. Status keeps the
-- code's names (agent_harness/contracts/records.py maps them to the deck's).
-- Applied by `claims_fnol_app.compose.migrate_agent_state`.

CREATE SCHEMA IF NOT EXISTS agent_state;

CREATE TABLE IF NOT EXISTS agent_state.approvals (
    id              text PRIMARY KEY,          -- the wait's workflow id
    action          text        NOT NULL,
    args            jsonb       NOT NULL,      -- every value as text
    args_digest     text        NOT NULL,      -- sha256: action, args, whose, key
    requested_for   text        NOT NULL,
    conversation_id text        NOT NULL DEFAULT '',
    idempotency_key text        NOT NULL,
    decided_by      text,
    expires_at      timestamptz NOT NULL,
    status          text        NOT NULL CHECK (status IN (
                        'assessing', 'waiting', 'carrying_out', 'done',
                        'failed', 'refused', 'expired', 'stale')),
    reason          text        NOT NULL DEFAULT '',
    created_at      timestamptz NOT NULL,
    updated_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS approvals_requested_for ON agent_state.approvals (requested_for);

CREATE TABLE IF NOT EXISTS agent_state.escalations (
    id              text PRIMARY KEY,          -- the wait's workflow id
    conversation_id text        NOT NULL,
    requested_for   text        NOT NULL,
    question        text        NOT NULL,
    found           text        NOT NULL DEFAULT '',
    missing         text        NOT NULL DEFAULT '',
    assignee        text,
    sla_due_at      timestamptz NOT NULL,
    status          text        NOT NULL CHECK (status IN ('queued', 'resolved', 'expired')),
    created_at      timestamptz NOT NULL,
    updated_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS escalations_requested_for
    ON agent_state.escalations (requested_for);
