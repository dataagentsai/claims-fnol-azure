-- Hand every object in the schemas :'schemas' (a text array) of the current
-- database to the role :'owner'. Included by roles.sql once per database.
--
-- Objects the admin made before A4 (the databases were created by it, and the
-- apps ran as it) change owner here; objects already the role's are left alone,
-- so a re-run does nothing. Never REASSIGN OWNED: it would also hand over every
-- other database the admin owns on the server.

SELECT format('ALTER SCHEMA %I OWNER TO %I', nspname, :'owner')
  FROM pg_namespace
 WHERE nspname = ANY (:'schemas'::text[])
   AND nspname <> 'public'          -- public belongs to pg_database_owner: the database's owner
   AND nspowner <> (SELECT oid FROM pg_roles WHERE rolname = :'owner') \gexec

-- Tables, views and the like; their indexes, TOAST tables and the sequences
-- their columns own move with them.
SELECT format('ALTER %s %s OWNER TO %I',
              CASE c.relkind WHEN 'v' THEN 'VIEW' WHEN 'm' THEN 'MATERIALIZED VIEW'
                             WHEN 'f' THEN 'FOREIGN TABLE' ELSE 'TABLE' END,
              c.oid::regclass, :'owner')
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE n.nspname = ANY (:'schemas'::text[])
   AND c.relkind IN ('r', 'p', 'v', 'm', 'f')
   AND c.relowner <> (SELECT oid FROM pg_roles WHERE rolname = :'owner') \gexec

-- Sequences no column owns (claim_number); queried after the tables moved.
SELECT format('ALTER SEQUENCE %s OWNER TO %I', c.oid::regclass, :'owner')
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE n.nspname = ANY (:'schemas'::text[])
   AND c.relkind = 'S'
   AND c.relowner <> (SELECT oid FROM pg_roles WHERE rolname = :'owner') \gexec

-- Functions and procedures (DBOS's notification triggers).
SELECT format('ALTER ROUTINE %s OWNER TO %I', p.oid::regprocedure, :'owner')
  FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
 WHERE n.nspname = ANY (:'schemas'::text[])
   AND p.prokind IN ('f', 'p')
   AND p.proowner <> (SELECT oid FROM pg_roles WHERE rolname = :'owner') \gexec

-- Enums, domains and other free-standing types (not a table's row type, not an
-- array type, which follow their owners).
SELECT format('ALTER %s %s OWNER TO %I',
              CASE t.typtype WHEN 'd' THEN 'DOMAIN' ELSE 'TYPE' END, t.oid::regtype, :'owner')
  FROM pg_type t JOIN pg_namespace n ON n.oid = t.typnamespace
 WHERE n.nspname = ANY (:'schemas'::text[])
   AND t.typtype IN ('e', 'd', 'r')
   AND t.typowner <> (SELECT oid FROM pg_roles WHERE rolname = :'owner') \gexec
