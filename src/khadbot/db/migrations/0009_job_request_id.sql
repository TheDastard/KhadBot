-- 0009_job_request_id.sql
-- The ID of the API request that queued a job (src/khadbot/jobs/status.py), so its worker log lines can be found from
-- the request's: the worker logs every job inside a context carrying it. A child job (an ingest's process jobs) takes
-- its parent's. Null for jobs queued outside a request (backfills, schedules, live report watches) and for older rows.

ALTER TABLE jobs ADD COLUMN request_id text;
