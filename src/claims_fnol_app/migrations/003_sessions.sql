-- A2: stored logins — a browser sign-in's refresh token, kept server-side so the
-- page can be given a fresh access token, and deleted at logout. The library's
-- table and store (agent_harness.state.postgres.PostgresSessionStore): the
-- refresh token is Fernet ciphertext under a key the process holds
-- (state.session_key, Key Vault), never the database, so a dump of
-- agent_state yields no usable token. Keyed by the login (`sub`), not the
-- policyholder; one row per login (FINDINGS F-91+). Idempotent.
CREATE TABLE IF NOT EXISTS agent_state.sessions (
    subject         text PRIMARY KEY,
    refresh_token   bytea       NOT NULL,
    updated_at      bigint      NOT NULL
);
