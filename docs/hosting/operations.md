# Operating the hosted service

## Signing out everywhere

`POST /v1/auth/forget` (the account menu's "Sign out everywhere") deletes the caller's stored Warcraft Logs tokens and ends every session of theirs, on every device. Ingests and live watches of theirs that fetch on their own token then stop with `wcl_not_signed_in`; signing in again stores new tokens. `POST /v1/auth/logout` ends only the session that calls it and keeps the tokens, so queued jobs still run.

## Rotating the token key

`KHADBOT_TOKEN_KEYS` lists Fernet keys, newest first: the first encrypts, every key decrypts. To replace a key (on a schedule, or because one may have leaked):

1. Put the new key first in `KHADBOT_TOKEN_KEYS`, keep the old one after it, and restart the API and the workers.
2. Run `python main.py rotate-token-keys` (in the image, `khadbot rotate-token-keys`) with the same environment. It re-encrypts every user's stored WCL tokens under the first key, taking each user's refresh lock, and prints how many users it rewrote. Running it twice is safe.
3. Remove the old key and restart. Tokens the old key alone could read are gone; a user in that state signs in to Warcraft Logs again.

Sign-ins in progress during a restart (the ten-minute OAuth state holds an encrypted verifier) fail once their key is removed and are started again by the user.

## Retention

What hosted mode keeps, and what removes the rest:

| Prefix | Kept | Removed by |
|---|---|---|
| `raw/` (raw WCL pages, zstd) | 14 days | the bucket's lifecycle rule |
| `annotated/` | 90 days | the bucket's lifecycle rule |
| `events/` (parquet) | always | nothing: every pull is re-processed from these |
| `records/` | while a read-model row references it, and for at least 30 days after it was written | the worker's cleanup job |

| Variable | Meaning |
|---|---|
| `KHADBOT_RETENTION_RAW_DAYS` | Days before `raw/` objects expire (default 14). Read by `scripts/data/apply_bucket_lifecycle.py`. |
| `KHADBOT_RETENTION_ANNOTATED_DAYS` | Days before `annotated/` objects expire (default 90). Same script. |
| `KHADBOT_RECORDS_MIN_AGE_DAYS` | A records object younger than this is never removed by the cleanup job (default 30). |
| `KHADBOT_RECORDS_CLEANUP_CRON` | When a worker queues the cleanup job, cron in UTC (default `17 3 * * *`). Empty turns the schedule off. |

### Bucket lifecycle rules

`scripts/data/apply_bucket_lifecycle.py` is in the image and reads the same `KHADBOT_S3_*` and `AWS_*` variables as the API:

    docker compose -f deploy/compose.yaml run --rm --entrypoint python worker scripts/data/apply_bucket_lifecycle.py --dry-run
    python scripts/data/apply_bucket_lifecycle.py            # on a platform shell, with the bucket's environment

It writes two enabled rules with ids `khadbot-expire-raw` and `khadbot-expire-annotated`, whose prefixes begin with `KHADBOT_S3_PREFIX` when one is set. A `PutBucketLifecycleConfiguration` replaces the bucket's whole configuration, so the script keeps every rule whose id does not start `khadbot-`, replaces its own, then reads the configuration back and exits 1 if a rule is missing, disabled or different. Run it again after changing a retention variable. The unit tests run it against moto; it has not been run against R2 or SeaweedFS from a development machine, so run it with `--dry-run` and then for real on staging and check the exit code.

What each provider supports (as documented; facts and sources):

- **Cloudflare R2: prefix expiration is supported.** A rule may name a prefix and delete objects after a number of days; the S3 calls `PutBucketLifecycleConfiguration`, `GetBucketLifecycleConfiguration` and `DeleteBucketLifecycle` are the way to manage rules. Objects are "typically removed ... within 24 hours" of their expiry, not at the instant, and a rule applied to a bucket that already holds old objects takes up to a day or more to remove them. The limit is 1000 rules per bucket. R2 lists the checksum-algorithm headers as unsupported on `PutBucketLifecycleConfiguration`, which is why the script only sends checksums where the API requires one. The API token needs the `Workers R2 Storage Write` permission. Sources: https://developers.cloudflare.com/r2/buckets/object-lifecycles/ and https://developers.cloudflare.com/r2/api/s3/api/
- **SeaweedFS 4.48 (the local stack): the gateway accepts prefix expiration rules, but the stack does not enforce them.** The S3 gateway implements `PutBucketLifecycleConfiguration`, `GetBucketLifecycleConfiguration` and `DeleteBucketLifecycle`; `Filter.Prefix` and `Expiration.Days` are supported (minimum 1 day), transition rules are rejected with `NotImplemented`, and expiry "is not event-time accurate" (budget up to a day). Rules are enforced by an S3 lifecycle worker, a scheduled job run through SeaweedFS's admin and worker processes (`weed/worker/tasks/s3_lifecycle` at tag 4.48), or by an opt-in TTL fast path enabled in `weed shell` (`s3.bucket.lifecycle.fastpath -name <bucket> -enable`, non-versioned buckets, rules with a prefix and `Expiration.Days`). `deploy/compose.yaml` runs `weed server -s3` alone, with neither, so in the local stack the rules are stored and read back but nothing expires. Locally, delete old objects yourself (`mise run stack:reset`) or enable the fast path. Sources: https://github.com/seaweedfs/seaweedfs/wiki/S3-Lifecycle, https://github.com/seaweedfs/seaweedfs/wiki/Amazon-S3-API, https://github.com/seaweedfs/seaweedfs/wiki/S3-API-FAQ, and the source at tag 4.48: https://github.com/seaweedfs/seaweedfs/blob/4.48/weed/s3api/s3lifecycle/DESIGN.md and https://github.com/seaweedfs/seaweedfs/blob/4.48/weed/worker/tasks/s3_lifecycle/handler.go

Both providers expire `raw/` and `annotated/` only by age; neither knows whether a pull still needs its raw pages. A pull whose raw pages expired is re-processed from its stored events (`python main.py backfill`), never refetched, so it costs no WCL points: a process job that finds its raw pages or fetch manifest gone keeps the stored events as final and builds the records from them, and fails with `pull_not_ingested` only when the pull has no stored events either. Re-ingesting it would be a new fetch.

### Backfill and the records cleanup

    python main.py backfill --encounter 3009 [--difficulty 5] [--since 2026-10-01] [--dry-run]

Run it after a config deploy that changed an encounter file or registry, or after a `RECORDS_VERSION` bump. It needs hosted mode and `DATABASE_URL` and queues one low-priority process job (priority -10, behind every other job on the `process` queue, app auth) for each projected pull of the encounter, from the `pulls` table: all difficulties unless `--difficulty` is given, and only reports that started on or after `--since` (UTC date; a report with no start time uses its fetch time). `--dry-run` only counts. A pull that already has a queued or running process job is skipped, so running it twice queues each pull once. The jobs are rebuilt from stored events and the annotation cache, checking the pull's input fingerprint against its fetched raw pages while they exist (an unchanged pull reads no page) and keeping the stored events once they expired; no WCL call is made, so it costs no points. A fetch-filter change needs a re-ingest instead, which spends points and which backfill never does.

The worker registers a periodic job, `khadbot.cleanup_records` on the `process` queue, on `KHADBOT_RECORDS_CLEANUP_CRON` (every worker with the schedule registers the same job; procrastinate defers it once per tick). It deletes `records/` objects that no `pulls.records_key` references and that were written at least `KHADBOT_RECORDS_MIN_AGE_DAYS` ago, and logs how many it examined, kept and deleted. Without the read-model tables (local mode) it does nothing. It never touches `events/`, `raw/` or `annotated/`.

## Usage ledger and observability

### The usage ledger

The `usage` table (migration `0008_usage.sql`) records what the service spends, with the user, WCL guild, report and job each row was for:

| Column | Meaning |
|---|---|
| `at`, `user_id`, `guild_id`, `report_code`, `job_id` | When, and who it was for. The guild is `reports.guild_id`, read at insert time; it is null until something fills that column. |
| `kind` | `wcl` or `llm`. |
| `wcl_points` | `wcl` rows: the points the ingest job measured. |
| `model`, `input_tokens`, `cached_input_tokens`, `cache_write_tokens`, `output_tokens`, `cost_usd` | `llm` rows. `input_tokens` includes the cached ones. `cost_usd` is an estimate from the price table, null for a model with no price. |

An ingest job writes its `wcl` row when it settles, from the same measured `points_spent` that the WCL budget settles to and `jobs.points_spent` stores. A job has at most one `wcl` row, and an offline ingest, which spends nothing, has none. A review job writes one `llm` row per model call (`khadbot.jobs.usage.record_llm_usage`, through a callback that reads each reply's `usage_metadata` and the model name it reports), with the job, the user who requested it and the report; a review answered from the cache writes none.

    python main.py usage --since 2026-10-01 [--by user|guild|kind]     # hosted mode, needs DATABASE_URL

It prints one line per group (default `kind`) with calls, WCL points, input, cached and output tokens and estimated cost, and a total.

Model prices are in `config/pricing/models.yaml`, with the page they came from and the date they were checked (2026-10-25, https://platform.claude.com/docs/en/about-claude/pricing). When Anthropic changes a price, or a new model is used, copy it from that page and update `checked:`.

### Logs

Hosted commands (`api`, `worker`, `migrate`, `backfill`, `usage`, `rotate-token-keys`) log one JSON object per line to stdout (`src/khadbot/logging_setup.py`). A field with no value is left out. The fields, in this order:

| Group | Fields | Source |
|---|---|---|
| Base | `time`, `level`, `logger`, `message` | Every record. |
| Process | `service` (the command: `api`, `worker`, ...), `version`, `env` | Set once at start; `version` and `env` from `KHADBOT_VERSION` and `KHADBOT_ENV` ([configuration](configuration.md)). |
| Context | `request_id`, `job_id`, `user_id`, `report_code`, `pull_key` | `log_context()` around the work, or `add_log_ids()` inside a `log_context_holder()` block for an ID learnt part-way through it (the API's signed-in user). |
| Event | `event`, `duration_ms`, `outcome`, `status`, `method`, `route`, `error_code`, `attempt`, `queue`, `job_kind`, `points`, `model`, `input_tokens`, `output_tokens`, `op`, `bytes` | `extra=` on the call. |
| Exception | `exc` | An exception's traceback. |

Code logs an event with `log.info("Job finished", extra={"event": "job.finish", "duration_ms": 412, "outcome": "done"})`. Only the names in `EVENT_FIELDS` are written, so a secret or an unbounded value passed in `extra=` never reaches the line; a new field is added to that allowlist and this table together. Numbers and booleans stay JSON numbers and booleans; any other value is written as text. Every message, exception text, context ID and text event field passes through a redaction step that masks passwords in URLs, values of secret-named pairs (`token`, `secret`, `password`, `key`, `cookie`, `session`, `authorization`), `Cookie`, `Set-Cookie` and `Authorization` header values, `Bearer` values, JWTs, Fernet-shaped keys and API key prefixes. That is a backstop: code still must not log them. The API runs uvicorn without its own log config, so uvicorn's lines go through the same handler, and with its access log off.

Each API request writes one `http.request` line instead (`src/khadbot/api/request_log.py`): `method`, `route` (the route template, such as `/v1/reports/{code}/night`, or `unmatched` for a path no route serves, never the raw path), `status`, `duration_ms`, `error_code` when the answer is an error, and `user_id` when the caller is signed in. It is INFO, WARNING for a 5xx and DEBUG for `/v1/health`. An unhandled exception is written as status 500 with `exc` and `error_code` `internal_error`, and the middleware answers it itself with a 500 `{"code": "internal_error", "message": ...}` body, so that answer carries `X-Request-ID` and the CORS headers like any other (an exception after the response has started can only be logged and re-raised). Once `auth.routes.optional_user` has resolved the caller, every later line of the request, the handler's included, carries `user_id` too. Every request has a `request_id`: the caller's `X-Request-ID` when it is 8 to 64 letters, digits and hyphens, otherwise 16 generated hex characters (an invalid value is never echoed). Every line written while the request is handled carries it, and the response returns it in `X-Request-ID`, which CORS exposes to the web app in both modes. To trace a user's report of a failure, search the logs for that ID.

The worker writes two events per run of a job (`src/khadbot/jobs/queue.py`), inside the job's context (`job_id`, `user_id`, `report_code`, `request_id`):

| Event | Fields | Level |
|---|---|---|
| `job.start` | `job_kind` (`ingest`, `process`, `review`), `queue`, `attempt` (1 on the first run) | INFO |
| `job.finish` | the same, plus `outcome`, `duration_ms`, `error_code` (failed, cancelled, retrying, interrupted) and `points` (an ingest's measured WCL points) | INFO; WARNING for `retrying` and `interrupted`; ERROR for `failed`, with the traceback when the run raised |
| `job.settled` | `job_kind` (`ingest`), `outcome` (`done`, `failed`, `cancelled`), `error_code`; written by the store (`src/khadbot/jobs/status.py`) in the worker or the API | INFO; ERROR for `failed` |

`outcome` is `done`, `failed`, `retrying` (queued again with backoff), `cancelled`, `interrupted` (the worker stopped mid-job), `waiting_budget` (parked until WCL points free up), `gone` (already finished or cancelled when picked up) or `awaiting_children` (an ingest whose process jobs are still queued or running). Each run writes exactly one `job.finish`, before the exception leaves the task. A run that ends without an exception takes its outcome and `error_code` from the row it left, so an ingest whose pulls all failed and queued nothing logs `failed` with `pulls_failed`. An ingest that logged `awaiting_children` gets one `job.settled` line, in its own context, when its last process job finishes or a cancel ends it; it says what the row ended as. Together, the two events' `done`, `failed` and `cancelled` outcomes count each job that ran once, as its row ended (an `interrupted` run's row is `failed` with that code). procrastinate's own "ended with status" line stays but without its traceback, so a failure's traceback appears once.

A job keeps the `request_id` of the API request that queued it in `jobs.request_id`, and an ingest's process jobs inherit it, so one ID finds the request's `http.request` line and every worker line of the jobs it caused. Work that starts outside a request generates its own ID the same way (16 hex characters): each live report watch poll has one, which its lines carry and the ingest it queues stores, and each backfill run has one for every process job it queues (`main.py backfill` prints it). A job already queued when a poll or backfill finds it keeps its own ID. The records cleanup queues no jobs and has none, nor do the worker's own lines outside a job.

| Variable | Meaning |
|---|---|
| `KHADBOT_LOG_FORMAT` | `json` (default) or `text` (plain lines, with the IDs and then the event fields after the message). The terminal commands (`cli`, `ingest`, `digest`, `serve`, `test-session`) default to `text`. |
| `KHADBOT_LOG_LEVEL` | Root log level (default `INFO`). `cli`, `ingest`, `digest` and `test-session` default to `WARNING` and write to stderr. |

### Error tracking

With `SENTRY_DSN` set ([configuration](configuration.md)), the API and the worker send their errors to a tracker through the Sentry SDK (`sentry-sdk`, in the `hosted` group); Sentry and GlitchTip both accept it. Without a DSN the SDK is never imported. `src/khadbot/error_reporting.py` starts it right after logging:

- An unhandled exception in a route, and any record logged at `ERROR` or above (a failed job), becomes an event. The API's request log middleware answers an unhandled exception itself, so it sends that event (`report_exception`), inside the request's context. `INFO` records become breadcrumbs on the next event; log lines are not sent as tracker logs.
- No user details, cookies, request headers, request bodies, query data or stack-frame variables are collected. Every string in an event or breadcrumb goes through the same redaction as the log lines, and a logged message's arguments are dropped in favour of the redacted formatted text.
- Each event is tagged with `service` and the `request_id`, `job_id`, `user_id`, `report_code` and `pull_key` of the work it happened in, so the tag finds the matching log lines. `release` is `KHADBOT_VERSION`; `environment` is `KHADBOT_ENV` (`unset` when empty).

The web app reports client-side errors the same way when built with `NEXT_PUBLIC_SENTRY_DSN` (`apps/web/src/instrumentation-client.ts`). Its events take `environment` from `NEXT_PUBLIC_KHADBOT_ENV` (`unset` when empty) and `release` from `NEXT_PUBLIC_KHADBOT_VERSION`, set at build time like the DSN; give them the same values as the API's `KHADBOT_ENV` and `KHADBOT_VERSION`.

### Worker health

A worker has no HTTP server. While it runs it touches a file every `KHADBOT_WORKER_HEARTBEAT_S` seconds from its event loop, and removes it when it stops. `python main.py worker --check` (in the image, `khadbot worker --check`) needs no database and exits 0 only if that file was touched within `KHADBOT_WORKER_STALE_S` seconds, so a crashed or hung worker fails it. The compose stack uses it as the worker's health check; on a platform, use it as the container health command. The API's health route is `/v1/health`.

| Variable | Meaning |
|---|---|
| `KHADBOT_WORKER_HEALTH_FILE` | The file (default `khadbot-worker.alive` in the temp directory). |
| `KHADBOT_WORKER_HEARTBEAT_S` | Seconds between touches (default 10). |
| `KHADBOT_WORKER_STALE_S` | Age at which the check fails (default 60). |
