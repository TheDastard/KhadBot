-- 0006_budget.sql
-- The WCL budget manager's state (src/khadbot/jobs/budget_store.py): one row per points bucket, holding the policy's
-- state document (jobs/budget.py, BudgetState): the bucket as last read from WCL's rateLimitData plus settlements
-- since, this window's reservations (job ID, user, WCL guild, estimate, measured spend) and the jobs waiting for
-- points. Every change reads the row with SELECT ... FOR UPDATE, so two workers never reserve the same points.
-- The state holds job and user IDs, never a token.

CREATE TABLE wcl_budget (
    bucket     text PRIMARY KEY CHECK (bucket IN ('app')),
    state      jsonb,                 -- NULL until the first change; the policy starts from the scheme's limit
    updated_at timestamptz NOT NULL
);
