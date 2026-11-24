-- deploy/observability/postgres/readonly.sql: the read-only role Grafana's Postgres data source signs in as.
--
-- Run by the obs-readonly service (deploy/compose.yaml) after the migrations, with psql variables `ro_password` and
-- `db`. Idempotent: it creates the role or resets its password, then (re)grants. Grants name the tables the
-- dashboards read; tables holding tokens, sessions or OAuth state (wcl_tokens, sessions, oauth_states) are left out,
-- so there are no default privileges for future tables. A new table a dashboard needs is added here.

SELECT format('CREATE ROLE khadbot_readonly LOGIN PASSWORD %L', :'ro_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'khadbot_readonly')
\gexec

SELECT format('ALTER ROLE khadbot_readonly LOGIN PASSWORD %L', :'ro_password')
\gexec

GRANT CONNECT ON DATABASE :"db" TO khadbot_readonly;
GRANT USAGE ON SCHEMA public TO khadbot_readonly;

GRANT SELECT ON
    jobs,
    usage,
    wcl_budget,
    watches,
    procrastinate_jobs,
    procrastinate_events,
    procrastinate_periodic_defers,
    procrastinate_workers
TO khadbot_readonly;
