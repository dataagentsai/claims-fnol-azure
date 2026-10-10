-- Tier 4a A4: a database login per app, each with the least it needs.
--
--   agent role    (claims_agent)   owns the agent's database: agent_state.*
--                                  (checkpoints, requests, approvals,
--                                  escalations) and DBOS's dbos.*, which DBOS
--                                  creates itself at launch, running as this
--                                  role. No CONNECT on the claims database.
--   system role   (claims_system)  owns the claims database and its tables. In
--                                  the agent's database: CONNECT, USAGE on
--                                  agent_state and SELECT on
--                                  agent_state.approvals, nothing else (F-51).
--
-- Run by the admin login only (on this Mac the `claims_fnol` role, on Azure the
-- server administrator), which needs CREATEROLE and CREATEDB. No app logs in as
-- it. Idempotent: run it again and it changes nothing, except to hand over
-- anything the admin made in the meantime and to reset the two passwords.
--
--   PGHOST=... PGUSER=<admin> PGPASSWORD=... PGDATABASE=postgres \
--   A4_AGENT_PASSWORD=... A4_SYSTEM_PASSWORD=... \
--   psql -X -q -v agent_role=claims_agent -v system_role=claims_system \
--        -v agent_db=claims_fnol_dbos -v claims_db=claims_fnol -f infra/sql/roles.sql
--
-- Callers: scripts/dev_roles.py (dev-up), infra/hooks/db-roles.sh (Azure
-- postprovision), tests/pg.py (every test that runs the apps on PostgreSQL).
-- The passwords come from the environment, never the command line, and no
-- error prints the statement that carried one (VERBOSITY terse).

\set ON_ERROR_STOP on
\set VERBOSITY terse
\set SHOW_CONTEXT never
\set QUIET on
SET client_min_messages TO warning;

\getenv agent_password A4_AGENT_PASSWORD
\getenv system_password A4_SYSTEM_PASSWORD
\if :{?agent_password}
\else
DO $$ BEGIN RAISE EXCEPTION 'A4_AGENT_PASSWORD is not set'; END $$;
\endif
\if :{?system_password}
\else
DO $$ BEGIN RAISE EXCEPTION 'A4_SYSTEM_PASSWORD is not set'; END $$;
\endif

-- 1. The two logins, made once; the password set every run (so .env or Key
--    Vault is always what the server holds).
SELECT set_config('a4.agent_role', :'agent_role', false),
       set_config('a4.system_role', :'system_role', false) \g /dev/null
DO $$
DECLARE
    r text;
BEGIN
    FOREACH r IN ARRAY ARRAY[current_setting('a4.agent_role'), current_setting('a4.system_role')]
    LOOP
        IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = r) THEN
            EXECUTE format('CREATE ROLE %I LOGIN NOCREATEDB NOCREATEROLE', r);
        END IF;
    END LOOP;
END $$;
ALTER ROLE :"agent_role" WITH LOGIN NOCREATEDB NOCREATEROLE PASSWORD :'agent_password';
ALTER ROLE :"system_role" WITH LOGIN NOCREATEDB NOCREATEROLE PASSWORD :'system_password';

-- The admin may act as both (PostgreSQL 16: a CREATEROLE role holds only ADMIN
-- on the roles it makes), so it can hand its objects over and run DDL as their
-- owner. Membership goes this way only: neither app role is a member of anything.
GRANT :"agent_role" TO CURRENT_USER WITH INHERIT TRUE, SET TRUE;
GRANT :"system_role" TO CURRENT_USER WITH INHERIT TRUE, SET TRUE;

-- 2. Each database to its app; no one else may connect, except the claims system
--    to the agent's database, to read approvals.
ALTER DATABASE :"agent_db" OWNER TO :"agent_role";
ALTER DATABASE :"claims_db" OWNER TO :"system_role";
REVOKE ALL ON DATABASE :"agent_db" FROM PUBLIC;
REVOKE ALL ON DATABASE :"claims_db" FROM PUBLIC;
GRANT CONNECT ON DATABASE :"agent_db" TO :"system_role";

-- 3. The agent's database. Whatever the admin made here before A4 (agent_state,
--    and dbos from a DBOS that ran as the admin) goes to the agent role.
\connect :agent_db
\set QUIET on
SET client_min_messages TO warning;
\set owner :agent_role
\set schemas '{public,agent_state,dbos}'
\ir hand-over.sql

-- The agent's own tables, made as the agent role, so the grant below has
-- something to name on a database no agent has started on yet. The same
-- idempotent files the app runs at start (claims_fnol_app.compose
-- .migrate_agent_state); tests/test_roles.py checks every one is listed here.
SET ROLE :"agent_role";
\ir ../../src/claims_fnol_app/migrations/001_agent_state.sql
\ir ../../src/claims_fnol_app/migrations/002_records.sql
RESET ROLE;

-- The claims system reads approvals and nothing else. Revoked first, so a
-- wider grant made by hand does not survive a re-run.
REVOKE ALL ON ALL TABLES IN SCHEMA agent_state FROM :"system_role";
REVOKE ALL ON ALL SEQUENCES IN SCHEMA agent_state FROM :"system_role";
SELECT format('REVOKE ALL ON ALL TABLES IN SCHEMA dbos FROM %I', :'system_role'),
       format('REVOKE ALL ON SCHEMA dbos FROM %I', :'system_role')
  FROM pg_namespace WHERE nspname = 'dbos' \gexec
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA agent_state TO :"system_role";
GRANT SELECT ON agent_state.approvals TO :"system_role";

-- 4. The claims database: everything in it to the system role.
\connect :claims_db
\set QUIET on
SET client_min_messages TO warning;
\set owner :system_role
\set schemas '{public}'
\ir hand-over.sql
