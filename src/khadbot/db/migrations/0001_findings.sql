-- 0001_findings.sql
-- Per-pull findings, keyed like the SQLite store: one row per findings cache key.

CREATE TABLE findings (
    cache_key  text PRIMARY KEY,
    pull_key   text NOT NULL,
    report     jsonb NOT NULL,
    flagged    boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL
);

CREATE INDEX findings_pull_key ON findings (pull_key);
