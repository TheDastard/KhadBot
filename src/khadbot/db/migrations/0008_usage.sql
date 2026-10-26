-- 0008_usage.sql
-- The usage ledger (src/khadbot/jobs/usage.py): one row per WCL spend or LLM call, with who it was for, so the
-- service can report cost by user, guild or kind. A 'wcl' row carries the points an ingest job measured; an 'llm'
-- row carries the model, its tokens and an estimated cost. No row holds a token or any other secret.

CREATE TABLE usage (
    id                  bigserial PRIMARY KEY,
    at                  timestamptz NOT NULL DEFAULT now(),
    user_id             text REFERENCES users (id) ON DELETE SET NULL,
    guild_id            bigint,                       -- the report's WCL guild; null until report metadata fills it
    report_code         text,
    job_id              text REFERENCES jobs (id) ON DELETE SET NULL,
    kind                text NOT NULL CHECK (kind IN ('wcl', 'llm')),
    wcl_points          double precision,             -- kind = 'wcl'
    model               text,                         -- kind = 'llm'
    input_tokens        bigint,                       -- all input tokens, cached ones included
    cached_input_tokens bigint,                       -- read from the prompt cache
    cache_write_tokens  bigint,                       -- written to the prompt cache
    output_tokens       bigint,
    cost_usd            double precision,             -- estimated from config/pricing/models.yaml; null if the model has no price
    CHECK ((kind = 'wcl' AND wcl_points IS NOT NULL) OR (kind = 'llm' AND model IS NOT NULL))
);

CREATE INDEX usage_at ON usage (at DESC);
CREATE INDEX usage_user ON usage (user_id, at DESC) WHERE user_id IS NOT NULL;
-- A job's WCL spend is written once: settling the same job again changes nothing.
CREATE UNIQUE INDEX usage_wcl_job ON usage (job_id) WHERE kind = 'wcl' AND job_id IS NOT NULL;
