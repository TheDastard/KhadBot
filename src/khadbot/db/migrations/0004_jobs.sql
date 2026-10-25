-- 0004_jobs.sql
-- The user-facing side of the job queue (src/khadbot/jobs/): one row per queued job with its state, per-pull
-- progress and typed failure. The queue itself (procrastinate's tables) is in the next migration. Every change to a
-- row also sends NOTIFY job_events with the job's ID, so a client can follow progress without polling.

CREATE TABLE jobs (
    id                text PRIMARY KEY,                -- KhadBot's own ID; job payloads carry this and nothing else
    kind              text NOT NULL CHECK (kind IN ('ingest', 'process', 'review')),
    report_code       text NOT NULL,
    pull_key          text,                            -- process and review jobs: '<report>:<fight>'
    parent_id         text REFERENCES jobs (id) ON DELETE CASCADE,  -- a process job's ingest
    state             text NOT NULL DEFAULT 'queued'
                      CHECK (state IN ('queued', 'running', 'waiting_budget', 'done', 'failed', 'cancelled')),
    -- {"stage": text, "fraction": number, "pulls": {"<pull_key>": {"stage", "fraction", "state", "error"}}}
    progress          jsonb NOT NULL DEFAULT '{"stage": null, "fraction": 0, "pulls": {}}',
    requested_by      text REFERENCES users (id) ON DELETE SET NULL,
    auth_kind         text NOT NULL CHECK (auth_kind IN ('app', 'user')),
    points_spent      double precision,
    error_code        text,
    error_message     text,
    attempts          integer NOT NULL DEFAULT 0,      -- how many times a worker started it
    cancel_requested  boolean NOT NULL DEFAULT false,  -- the running job stops at its next page boundary
    awaiting_children boolean NOT NULL DEFAULT false,  -- the job's own work is done; it finishes with its process jobs
    queue_job_id      bigint,                          -- procrastinate's job; no foreign key, its tables are separate
    spec              jsonb NOT NULL,                  -- the job spec (jobs/specs.py); a user ID, never a token
    created_at        timestamptz NOT NULL,
    started_at        timestamptz,
    finished_at       timestamptz,
    updated_at        timestamptz NOT NULL
);

CREATE INDEX jobs_report ON jobs (report_code, created_at DESC);
CREATE INDEX jobs_parent ON jobs (parent_id) WHERE parent_id IS NOT NULL;
CREATE INDEX jobs_user ON jobs (requested_by, created_at DESC) WHERE requested_by IS NOT NULL;
-- "Is an ingest of this report already queued or running?" is asked on every ingest request.
CREATE INDEX jobs_active_ingest ON jobs (report_code) WHERE kind = 'ingest' AND state IN ('queued', 'running', 'waiting_budget');
