# Hosting

How the hosted service is packaged and configured: the container image, the environment variables each component reads, and the local stack that runs them. Local mode (the CLI on your machine) needs none of this. The reasons behind these choices are in `docs/decisions.md` (container image and local stack).

## The image

One image holds every role. `deploy/Dockerfile` builds it from the repository root:

    docker build -f deploy/Dockerfile -t khadbot .

- Built with uv from `uv.lock` on the Python version the base image provides (`.python-version` names it), installing only the `api` and `hosted` groups. No CLI, LLM provider packages or dev tools.
- Carries `src/`, `config/` (the authored encounter files and registries: every cache key hashes them, so the image and the stored data must agree on them), `main.py` and the seed script.
- Runs as the non-root user `khadbot` (uid 10001). `KHADBOT_DATA_DIR` is scratch space inside the container; hosted mode keeps its data in Postgres and the bucket.
- The command picks the role: `api` (the default, port 8000), `migrate` (apply migrations and exit), `worker` (the job queue worker).
- `.dockerignore` excludes everything except the files the image copies, so `.env` and `.khadbot_data*` can never enter the build context.

Image size: not measured yet. The `stack` CI job prints it (`docker image ls khadbot:local`); record the figure here once a build has run.

## Environment variables

Secrets are supplied by the platform's environment, never by the image, a compose file or the logs. `deploy/stack.env` holds throwaway values for the local stack only.

### API (`khadbot api`)

| Variable | Meaning |
|---|---|
| `KHADBOT_MODE` | `hosted` for the hosted API: sign-in, report access checks, ingest jobs, findings and the night summary from Postgres. Unset or `local` serves a local data directory with no sign-in. |
| `DATABASE_URL` | Postgres connection URL. Required in hosted mode. |
| `KHADBOT_DB_POOL_MIN`, `KHADBOT_DB_POOL_MAX` | Connection pool bounds (defaults 1 and 10). |
| `KHADBOT_STORAGE` | `s3` for the bucket (events, annotations, records and raw pages). `local` keeps files under `KHADBOT_DATA_DIR`. |
| `KHADBOT_S3_BUCKET` | Bucket name. Required with `s3`; the bucket must already exist. |
| `KHADBOT_S3_PREFIX` | Optional key prefix inside the bucket. |
| `KHADBOT_S3_ENDPOINT` | Endpoint URL of the S3-compatible service (Cloudflare R2 in production, SeaweedFS locally). Empty means AWS S3. |
| `KHADBOT_S3_REGION` | Region (`auto` for R2, any value for SeaweedFS). |
| `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` | Bucket credentials, read by boto3 itself. |
| `KHADBOT_DATA_DIR` | Scratch directory (the image sets `/var/lib/khadbot`). Not used for data in hosted mode. |

In hosted mode the API also reads the sign-in settings (`WARCRAFTLOGS_CLIENT_ID`, `WARCRAFTLOGS_CLIENT_SECRET`, `KHADBOT_TOKEN_KEYS`, `KHADBOT_WCL_REDIRECT_URI`, `KHADBOT_POST_LOGIN_URL`, `KHADBOT_WEB_ORIGINS` and the session and access settings in `.env.example`) and refuses to start without the client credentials and a token key. It calls WCL only to check whether a user may read a report (cached in `report_access`) and to sign users in; it never fetches events. It queues ingests on the job queue and never runs them.

| Variable | Meaning |
|---|---|
| `KHADBOT_WEB_ORIGINS` | The web app's origins. CORS allows exactly these, with credentials, `GET`, `POST`, `DELETE` and the `Content-Type`, `X-KhadBot-CSRF` and `Last-Event-ID` headers; state-changing routes also refuse a browser `Origin` outside this list. |
| `KHADBOT_SSE_KEEPALIVE_S` | How often an idle job event stream sends a comment line and re-reads the job (default 15). Keep it under your proxy's idle timeout. |

The routes: every report, pull, timeline, events, findings and evidence route answers 404 `report_not_found` unless the caller (signed in or not) can read the report; `GET /v1/reports` lists the signed-in user's own reports; `POST /v1/reports` queues an ingest, `GET /v1/jobs/{id}`, `GET /v1/jobs/{id}/events` (server-sent events) and `POST /v1/jobs/{id}/cancel` follow and cancel it, for the user who started it only. On Windows the hosted API runs on a selector event loop (psycopg's async connections need it). A proxy in front of the API must not buffer `text/event-stream` responses (the API sends `X-Accel-Buffering: no`).

### Migrate (`khadbot migrate`)

`DATABASE_URL` (and the pool settings above). It applies the numbered files in `src/khadbot/db/migrations/` under an advisory lock and exits, so running it twice is safe. The job queue's tables (procrastinate's schema) are one of those files.

### Worker (`khadbot worker`)

Reads everything the API does, plus the WCL credentials once jobs fetch from WCL: `WARCRAFTLOGS_CLIENT_ID` and `WARCRAFTLOGS_CLIENT_SECRET`, and the LLM settings for reviews (`LLM_PROVIDER`, the provider's API key, `ANALYST_MODEL`). The worker takes jobs from Postgres (`python main.py worker [--queues ingest,process] [--concurrency N]`), keeps its own connection pool to `DATABASE_URL` beside the one above, and stops gracefully on SIGINT or SIGTERM. Its own settings:

| Variable | Meaning |
|---|---|
| `KHADBOT_WORKER_QUEUES` | Queues to take jobs from (default `ingest,process,review,watch`). Run an ingest worker and a review worker apart by giving each its own list. An `ingest` job only fetches (WCL requests and raw-page writes, several pulls at once); a `process` job normalizes one pull's raw pages into its event table (about a second of CPU, in a thread) and builds its records, so CPU goes to the `process` queue. |
| `KHADBOT_WORKER_CONCURRENCY` | Jobs run at once per worker (default 2). |
| `KHADBOT_WORKER_SHUTDOWN_TIMEOUT_S` | How long a stopping worker waits for running jobs before interrupting them (default 60). An interrupted job is marked `failed` with the code `interrupted`; request it again and it resumes from the raw page cache. |
| `KHADBOT_JOB_MAX_RETRIES` | Retries after a WCL rate limit, a WCL network error or a sign-in that could not be renewed (default 5). Other failures are not retried. |
| `KHADBOT_JOB_RETRY_BACKOFF_S` | Base of the wait between retries: the wait is this value to the power of the attempt, in seconds (default 3). |
| `KHADBOT_JOB_PROGRESS_INTERVAL_S` | How often a running job writes its progress and checks whether it was cancelled (default 0.5). |

The WCL budget (`khadbot.jobs.budget`, state in the `wcl_budget` row). A worker reads the app's `rateLimitData` when it starts and after every job that reserved points (about 1 point a read, with the app's own credentials); a failed read is logged and the budget keeps its own count. `GET /v1/budget` shows it to a signed-in user. Settings, all optional:

| Variable | Meaning |
|---|---|
| `KHADBOT_WCL_LIMIT_SCHEME` | `current` (default) or `new`: how WCL charges the account that owns the client (`docs/wcl-api.md`, "Rate limits and point types"). Until the first `rateLimitData` read the bucket assumes the scheme's documented limit, 3,600 or 1,000 points an hour; after it, WCL's own limit, so a subscription shows up without a deploy. |
| `KHADBOT_BUDGET_USER_TOKENS_SPEND_APP` | `true` (default): a job on a signed-in user's token counts in full against the app's points, as the budget probe measured. `false` needs the `new` scheme and only changes anything once `KHADBOT_BUDGET_USER_POINTS_PER_HOUR` is set. |
| `KHADBOT_BUDGET_GUILD_SHARE`, `KHADBOT_BUDGET_USER_SHARE` | Share of the hourly points one WCL guild and one user may use per window (defaults 0.25 and 0.10), user-token jobs included. |
| `KHADBOT_BUDGET_HEADROOM_SHARE` | Share kept free for live raid ingest (default 0.20). |
| `KHADBOT_BUDGET_POINTS_PER_PULL`, `KHADBOT_BUDGET_POINTS_PER_SINGLE_PULL`, `KHADBOT_BUDGET_POINTS_PER_JOB` | The estimate a job reserves: 7.2 per batched pull (a 25-pull night is about 180 points after cross-fight batching), 14 for a pull fetched alone, plus 4 for the report metadata and rate-limit reads. |
| `KHADBOT_BUDGET_DEFAULT_PULLS` | Pulls assumed for a whole-report ingest whose metadata isn't cached yet (default 25). |
| `KHADBOT_BUDGET_DATABASE_POINTS_PER_HOUR` | The `new` scheme's database points (default 250). Ingest uses none. |
| `KHADBOT_BUDGET_USER_POINTS_PER_HOUR` | A signed-in user's own pool on the `new` scheme; unset until it is measured. |
| `KHADBOT_BUDGET_RESERVATION_TTL_S` | A reservation no job settled (a worker died) is dropped after this long (default 7200). |

Jobs are rows in the `jobs` table (state, per-pull progress, typed error) and each change sends `NOTIFY job_events` with the job's ID. The code that queues and cancels jobs is `khadbot.jobs.queue.JobQueue`; the API calls it for the ingest routes.

### Live report watch

A signed-in user can watch a report that is still being logged (`POST /v1/reports/{code}/watch`; `GET` shows the watch and `DELETE` stops it). The watch polls the report's metadata on the user's own WCL token and queues an ingest of each new boss fight (`docs/decisions.md`, D64). It runs on the `watch` queue, which workers take jobs from by default; a worker started with `--queues` or `KHADBOT_WORKER_QUEUES` must list `watch` to run polls. Settings, all optional:

| Variable | Meaning |
|---|---|
| `KHADBOT_WATCH_POLL_INTERVAL_S` | Seconds between metadata reads of a watched report (default 180). |
| `KHADBOT_WATCH_QUIET_PERIOD_S` | A watch stops when no new boss fight turned up for this long (default 2700). |
| `KHADBOT_WATCH_MAX_LENGTH_S` | A watch stops after this long in all (default 21600). |
| `KHADBOT_WATCH_POLL_POINTS` | Points one poll reserves from the WCL budget (default 1, not measured yet; `docs/wcl-api.md`, "Live watch polls"). Never below 1. |
| `KHADBOT_WATCH_MAX_FAILURES` | A watch stops after this many failed polls in a row (default 5). |

Polls and the ingests a watch queues are live requests to the WCL budget, which may use the headroom (`KHADBOT_BUDGET_HEADROOM_SHARE`). A poll the budget has no points for is skipped; the watch shows it in `polls_skipped`. Watches are rows in the `watches` table (migration 0007), one active per report.

### Seed (`scripts/data/seed_stack.py`)

The API's variables. It refuses to run unless `KHADBOT_MODE=hosted` and `KHADBOT_STORAGE=s3`, so it can never write a local data directory. It needs no WCL credentials.

### Containers in the local stack

| Variable | Meaning |
|---|---|
| `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` | The Postgres container's initial user and database. `DATABASE_URL` must match. |
| `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` | Also the one S3 identity the SeaweedFS container creates at start, so KhadBot and the store agree. |

## The local stack

Needs Docker with Compose. From the repository root:

    mise run stack:up       # build the image; start Postgres, SeaweedFS, the migrate step and the API
    mise run stack:seed     # load the recorded fixture reports (offline, no WCL calls)
    mise run stack:smoke    # health, a signed-out 404, then the report list and a night summary as the smoke user
    mise run stack:queue-smoke  # queue a job and check the worker finishes it
    mise run stack:logs
    mise run stack:down     # stop, keep the data volumes
    mise run stack:reset    # stop and delete the database and bucket

Everything listens on 127.0.0.1: the API on 8000, Postgres on 5432 and the SeaweedFS S3 endpoint on 8333. The data lives in named Docker volumes; the local `.khadbot_data` directory is never mounted.

### Seeding

The seed also provisions a smoke user whose session cookie value is `KHADBOT_STACK_SMOKE_SESSION` (set only in `deploy/stack.env`) with a cached "readable" answer for each seeded report, so `stack:smoke` can read them without a Warcraft Logs sign-in, and caches each report as not public, so a signed-out visitor gets 404 without asking WCL. The readable answers stand for an hour (`KHADBOT_ACCESS_RECHECK_S`) and the not-public ones for 5 minutes (`KHADBOT_ACCESS_DENIED_RECHECK_S`); the stack's placeholder WCL credentials cannot renew either, so a visitor's request after that gets 503, and the routes answer 503 for everyone once the hour is up. Seed again to reset them.

`stack:seed` runs `scripts/data/seed_stack.py` in the image with the fixtures mounted read-only from the repository. For each report it ingests the pulls offline into the bucket, stores the report metadata, then runs the same process job the queue runs: annotate, build and store the records, project the read-model tables. A second run changes nothing.

It seeds the synthetic report `SYNTH01` and every real recorded report whose pages are present (`git lfs pull`). The real reports are large (about 200 MB of pages); for a quick seed pass the report code:

    docker compose -f deploy/compose.yaml --profile seed run --rm seed --only SYNTH01

The seed runs the job functions in-process rather than queueing them: SYNTH01 needs its own filters, encounter file and registries, which the stack's worker (built from the configuration) does not have.

### The queue smoke test

`stack:queue-smoke` runs `scripts/dev/queue_smoke.py` in the image. It queues an offline ingest of a report that is in no cache and waits for the stack's worker to fail it with the code `not_in_cache`. That exercises enqueue, the queue tables, the worker, the `jobs` row and typed failures on the real stack, with no WCL call. Ingesting a fixture report through a worker is covered by the hosted tests (`tests/integration/hosted/test_queue_pg.py`).

### CI

The `stack` job in `.github/workflows/check.yml` builds the image (nothing is pushed), starts the stack, seeds `SYNTH01`, runs the smoke script and then the queue smoke test against the stack's worker. It is the only place the image is built on every push.

## Local development

There are two ways to run KhadBot on your own machine.

**Local mode** is the CLI plus `main.py serve`: the dashboard API's read routes over `KHADBOT_DATA_DIR`, on 127.0.0.1, with no database, bucket, queue, Docker or sign-in. It is a quick look at a data directory. It is not a user surface: nothing in it signs in, ingests or runs a job.

**The full experience** (sign-in, ingest with live progress, live watch, the budget, reviews and chat) runs on the local test environment: hosted mode on your own machine, with Postgres and the bucket in Docker (see "The local stack" above) and the same API and worker code that production runs. Local mode is described first; the dev loop for the test environment follows it.

**What local mode serves.** The read routes (`/v1/reports`, `/v1/reports/{code}`, `/night`, the pull `digest`, `timeline`, `events` and `findings`, and `/v1/evidence/{ref}`) over the data directory, and `GET /v1/health`. Findings are the cached ones; nothing runs the analyst or calls Warcraft Logs. There is no sign-in, so every request may read every ingested report. CORS allows `http://localhost:3000` and `http://127.0.0.1:3000` for `GET`, without credentials. The hosted-only routes (`/v1/auth/*`, `/v1/me`, `/v1/jobs/*`, `/v1/reports/{code}/watch`, `/v1/budget`) are not mounted and answer 404 like any unknown route.

**What `/v1/health` reports.** `mode` is `local` or `hosted`, given by the entry point: `main.py serve` says `local`, `main.py api` in hosted mode says `hosted`. It is a label only. `features` has one boolean per optional area, true only when its routes are mounted, and clients branch on `features` only (`.claude/rules/web.md`): with `sign_in` off the web app shows the reports without a session and labels the header "Local", and a direct link to an area whose flag is off shows "Not available on this server yet".

    serve   {"mode": "local",  "features": {"sign_in": false, "ingest_jobs": false, "live_watch": false, "budget": false, "reviews": false, "chat": false}}
    api     {"mode": "hosted", "features": {"sign_in": true,  "ingest_jobs": true,  "live_watch": true,  "budget": true,  "reviews": false, "chat": false}}

**Steps.**

1. Build a data directory from the recorded fixture reports (needs `git lfs pull`; offline, about 5 seconds): `mise run gen:fixture-data` writes `.khadbot_fixture_data`. It ingests every real recorded report, stores their metadata and digests each pull, so the annotation cache and pull records are filled. It never calls Warcraft Logs and ignores `KHADBOT_MODE`, `DATABASE_URL` and the S3 settings. The synthetic `SYNTH01` report is only in the stack, because it needs an encounter file of its own.
2. Serve it: `mise run serve:fixtures` runs `main.py serve` on `http://127.0.0.1:8000` over `.khadbot_fixture_data`. To serve your own data, set `KHADBOT_DATA_DIR` first (for example to `.khadbot_data`); the task uses it when it is set.
3. Point the web dev server at it with `NEXT_PUBLIC_API_BASE=http://127.0.0.1:8000`. The web app reads `/v1/health`, finds sign-in off, shows the reports without a session and labels the header "Local".

**One report, for CI.** The recorded reports are about 200 MB of Git LFS pages, which a CI job need not fetch on every run. `--only` builds just the named reports; the smallest, `QkVa6GHRK8yfrTbw` (one pull, about 10 MB), is enough for the read screens:

    git lfs pull --include "tests/fixtures/encounter/wcl/QkVa6GHRK8yfrTbw/**"
    uv run python scripts/data/build_fixture_data.py --out .khadbot_fixture_data --only QkVa6GHRK8yfrTbw

A named report whose pages are missing fails the build with the `git lfs pull --include` line to run, and nothing is written. `SYNTH01` can't be named here (see step 1).

Check it: `curl http://127.0.0.1:8000/v1/health` shows `"mode":"local"` and every feature false. Serving your own `.khadbot_data` is read-only apart from the caches a digest fills; use a copy if you want it untouched.

### The dev loop (the full experience)

The full experience runs hosted mode on your machine: Postgres and the bucket in Docker, the API and the worker natively, so a Python edit takes effect in seconds and no image is built. It runs the code production runs, against a real database and bucket. Needs Docker Desktop running (`scripts/dev/setup.ps1` checks it; `-WithDocker` installs it) and `uv sync --extra dev` (the reload uses `watchfiles`).

    mise run dev:all        # Postgres and SeaweedFS in Docker, the bucket, migrations, then the API and the worker
    mise run dev:seed -- --only SYNTH01   # in a second terminal; without --only, every recorded report
    mise run dev:smoke      # health, a signed-out 404, a night summary, and a queued job the worker finishes

`dev:all` prints `dev: ready at http://127.0.0.1:8000` when the API answers and keeps running; the API's and the worker's logs share the terminal with `[api]` and `[worker]` prefixes. Ctrl-C stops both. The pieces run on their own too: `dev:infra` (the Docker services, the bucket and migrations, then exits), `dev:api` and `dev:worker` (each in the foreground, with reload). The web dev server joins `dev:all` when the web app exists.

**Settings.** `deploy/dev.env` (gitignored; `setup.ps1` creates it from `deploy/dev.env.example`, or copy it by hand) holds the same throwaway values as `deploy/stack.env` with `127.0.0.1` in place of the container host names. Use `127.0.0.1`, not `localhost`, for Postgres and the bucket: on Windows the connection pool stalls on its IPv6 attempt for `localhost`. The runner lays dev.env over the main checkout's `.env` (found the way the app finds it, so a worktree needs none of its own), which still supplies the WCL credentials, `LLM_PROVIDER` and the rest. With no WCL credentials anywhere, placeholders let the API start; everything but signing in to Warcraft Logs then works. The worker asks WCL for an app token when it starts (no points); a worker started without real credentials logs a warning that it could not read WCL's rate limit and carries on.

**Reload.** `main.py api --reload` and `main.py worker --reload` (what the tasks run) watch `src/khadbot` and `config/` for Python and YAML changes and restart the command; each prints `[reload] <file> changed; restarting`. A restart takes about 3 seconds. It stops the old process hard (on Windows a signal cannot ask it to finish), so a job the worker was running stays in its running state: cancel it, or `mise run stack:reset`. `--reload` needs the `dev` extra and is for development only; containers never use it.

**Sign in.** Real Warcraft Logs sign-in needs your own client in `.env` and a browser: the API runs on `localhost:8000`, whose callback (`KHADBOT_WCL_REDIRECT_URI` in dev.env) is registered with Warcraft Logs. This has not been exercised on the dev loop yet. Without a browser, the seed provisions the smoke user whose cookie `khadbot_session=local-stack-smoke-session` reads the seeded reports (see "Seeding"). Test sessions, which replace this for the web app, come with their own task.

**Port 8000 is shared.** `mise run stack:up` and `dev:all` both want it, and the dev tasks refuse to start while something listens there. Run `mise run stack:down` before the dev loop, or the other way round. They share the same Postgres and bucket volumes (the infrastructure is the stack's `postgres` and `seaweedfs` services, in the same compose project), so seeded data carries over, and `stack:down` also stops the dev loop's containers.

**Reset.** `mise run stack:reset` stops everything in the project and deletes the database and the bucket; `dev:all` recreates them, and `dev:seed` fills them again.

**Switches for development.** WCL replay, the fake model provider and test sessions come with their own tasks and are documented here when they land.

**Time to start.** With the images pulled and the volumes in place, `dev:all` has the infrastructure ready in about 3 seconds and the API healthy about 8 seconds after the command (measured on the maintainer's Windows machine, 2026-10-31). The first run also pulls the Postgres and SeaweedFS images, which takes around a minute depending on the connection.

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

An ingest job writes its `wcl` row when it settles, from the same measured `points_spent` that the WCL budget settles to and `jobs.points_spent` stores. A job has at most one `wcl` row, and an offline ingest, which spends nothing, has none. Model calls are written by `khadbot.jobs.usage.record_llm_usage`.

    python main.py usage --since 2026-10-01 [--by user|guild|kind]     # hosted mode, needs DATABASE_URL

It prints one line per group (default `kind`) with calls, WCL points, input, cached and output tokens and estimated cost, and a total.

Model prices are in `config/pricing/models.yaml`, with the page they came from and the date they were checked (2026-10-25, https://platform.claude.com/docs/en/about-claude/pricing). When Anthropic changes a price, or a new model is used, copy it from that page and update `checked:`.

### Logs

Hosted commands (`api`, `worker`, `migrate`, `backfill`, `usage`) log one JSON object per line to stdout: `time`, `level`, `logger`, `message`, and `job_id`, `user_id`, `report_code` when the line is written while a job runs, plus `exc` for an exception. Every message and exception text passes through a redaction step that masks passwords in URLs, values of secret-named pairs (`token`, `secret`, `password`, `key`, `cookie`, `session`, `authorization`), `Cookie`, `Set-Cookie` and `Authorization` header values, `Bearer` values, JWTs, Fernet-shaped keys and API key prefixes. That is a backstop: code still must not log them. The API runs uvicorn without its own log config, so its access lines go through the same handler.

| Variable | Meaning |
|---|---|
| `KHADBOT_LOG_FORMAT` | `json` (default) or `text` (plain lines, with the IDs after the message). |
| `KHADBOT_LOG_LEVEL` | Root log level (default `INFO`). |

There is no error tracker; `sentry-sdk` was not added.

### Worker health

A worker has no HTTP server. While it runs it touches a file every `KHADBOT_WORKER_HEARTBEAT_S` seconds from its event loop, and removes it when it stops. `python main.py worker --check` (in the image, `khadbot worker --check`) needs no database and exits 0 only if that file was touched within `KHADBOT_WORKER_STALE_S` seconds, so a crashed or hung worker fails it. The compose stack uses it as the worker's health check; on a platform, use it as the container health command. The API's health route is `/v1/health`.

| Variable | Meaning |
|---|---|
| `KHADBOT_WORKER_HEALTH_FILE` | The file (default `khadbot-worker.alive` in the temp directory). |
| `KHADBOT_WORKER_HEARTBEAT_S` | Seconds between touches (default 10). |
| `KHADBOT_WORKER_STALE_S` | Age at which the check fails (default 60). |
