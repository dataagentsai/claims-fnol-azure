-- The agent's own state (Tier 2), in the agent's database (claims_fnol_dbos),
-- beside the DBOS system tables the approval and escalation waits keep there.
-- Never the claims system's database: the agent reaches the claims system only
-- over MCP.
--
-- The tables the harness's PostgreSQL adapters write (agent_harness.state.postgres
-- and agent_harness.requests.postgres), as the reference agent's sql/001_schemas.sql
-- declares them. Applied by `claims_fnol_app.store.migrate`.

CREATE SCHEMA IF NOT EXISTS agent_state;

-- One row per run; read back by conversation (F-006), erasable by customer (F-056).
CREATE TABLE IF NOT EXISTS agent_state.checkpoints (
    run_id          text PRIMARY KEY,
    conversation_id text,
    customer_id     text,
    state           bytea       NOT NULL,
    updated_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS checkpoints_customer ON agent_state.checkpoints (customer_id);
CREATE INDEX IF NOT EXISTS checkpoints_conversation
    ON agent_state.checkpoints (conversation_id, updated_at DESC);

-- One name, one outcome (T-062): the delivery claim and the tool-call ledger.
CREATE TABLE IF NOT EXISTS agent_state.requests (
    name        text PRIMARY KEY,
    scope       text        NOT NULL,
    state       text        NOT NULL,
    outcome     jsonb,
    expires_at  timestamptz,
    recorded_at timestamptz NOT NULL DEFAULT now()
);
