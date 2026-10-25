-- 0007_watches.sql
-- Live report watches (src/khadbot/jobs/watch.py): one row per watch of a report that is still being logged. A
-- watch polls the report's metadata on a user's own WCL token and queues an ingest of each new boss fight. The row
-- holds the watch's schedule, what it has already handed to ingest and why it stopped; the queue job that runs the
-- next poll is procrastinate's (queue_job_id, no foreign key). A user ID, never a token.

CREATE TABLE watches (
    id             text PRIMARY KEY,
    report_code    text NOT NULL,
    user_id        text NOT NULL REFERENCES users (id) ON DELETE CASCADE,  -- whose WCL token every poll uses
    state          text NOT NULL DEFAULT 'active' CHECK (state IN ('active', 'stopped')),
    stop_reason    text CHECK (stop_reason IN ('user', 'quiet', 'max_length', 'sign_in', 'report_unavailable', 'errors')),
    started_at     timestamptz NOT NULL,
    expires_at     timestamptz NOT NULL,           -- started_at + the maximum length
    quiet_since    timestamptz NOT NULL,           -- the last time a new boss fight turned up (the start, before any)
    last_polled_at timestamptz,
    next_poll_at   timestamptz,
    polls          integer NOT NULL DEFAULT 0,     -- metadata reads that reached WCL
    polls_skipped  integer NOT NULL DEFAULT 0,     -- polls the WCL budget had no points for
    failures       integer NOT NULL DEFAULT 0,     -- consecutive polls that failed; reset by a poll that worked
    fights         jsonb NOT NULL DEFAULT '{}',    -- {"<fight id>": end_ms} of boss fights already handed to ingest
    ingests        integer NOT NULL DEFAULT 0,     -- ingest jobs this watch queued
    last_job_id    text,                           -- the latest ingest it queued or found already covering a fight
    points_spent   double precision NOT NULL DEFAULT 0,  -- the polls' estimated points (ingests count on their jobs)
    error_message  text,                           -- the last failed poll, safe to show
    queue_job_id   bigint,
    updated_at     timestamptz NOT NULL,
    stopped_at     timestamptz
);

-- One watch per report: a second request finds the first.
CREATE UNIQUE INDEX watches_one_active ON watches (report_code) WHERE state = 'active';
CREATE INDEX watches_user ON watches (user_id, started_at DESC);
