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
| `KHADBOT_MODE` | `hosted` to read findings and the night summary from Postgres. Unset or `local` serves a local data directory. |
| `DATABASE_URL` | Postgres connection URL. Required in hosted mode. |
| `KHADBOT_DB_POOL_MIN`, `KHADBOT_DB_POOL_MAX` | Connection pool bounds (defaults 1 and 10). |
| `KHADBOT_STORAGE` | `s3` for the bucket (events, annotations, records and raw pages). `local` keeps files under `KHADBOT_DATA_DIR`. |
| `KHADBOT_S3_BUCKET` | Bucket name. Required with `s3`; the bucket must already exist. |
| `KHADBOT_S3_PREFIX` | Optional key prefix inside the bucket. |
| `KHADBOT_S3_ENDPOINT` | Endpoint URL of the S3-compatible service (Cloudflare R2 in production, SeaweedFS locally). Empty means AWS S3. |
| `KHADBOT_S3_REGION` | Region (`auto` for R2, any value for SeaweedFS). |
| `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` | Bucket credentials, read by boto3 itself. |
| `KHADBOT_DATA_DIR` | Scratch directory (the image sets `/var/lib/khadbot`). Not used for data in hosted mode. |

The API makes no WCL calls and needs no WCL credentials.

### Migrate (`khadbot migrate`)

`DATABASE_URL` (and the pool settings above). It applies the numbered files in `src/khadbot/db/migrations/` under an advisory lock and exits, so running it twice is safe. The job queue's tables (procrastinate's schema) are one of those files.

### Worker (`khadbot worker`)

Reads everything the API does, plus the WCL credentials once jobs fetch from WCL: `WARCRAFTLOGS_CLIENT_ID` and `WARCRAFTLOGS_CLIENT_SECRET`, and the LLM settings for reviews (`LLM_PROVIDER`, the provider's API key, `ANALYST_MODEL`). The worker takes jobs from Postgres (`python main.py worker [--queues ingest,process] [--concurrency N]`), keeps its own connection pool to `DATABASE_URL` beside the one above, and stops gracefully on SIGINT or SIGTERM. Its own settings:

| Variable | Meaning |
|---|---|
| `KHADBOT_WORKER_QUEUES` | Queues to take jobs from (default `ingest,process,review`). Run an ingest worker and a review worker apart by giving each its own list. |
| `KHADBOT_WORKER_CONCURRENCY` | Jobs run at once per worker (default 2). |
| `KHADBOT_WORKER_SHUTDOWN_TIMEOUT_S` | How long a stopping worker waits for running jobs before interrupting them (default 60). An interrupted job is marked `failed` with the code `interrupted`; request it again and it resumes from the raw page cache. |
| `KHADBOT_JOB_MAX_RETRIES` | Retries after a WCL rate limit, a WCL network error or a sign-in that could not be renewed (default 5). Other failures are not retried. |
| `KHADBOT_JOB_RETRY_BACKOFF_S` | Base of the wait between retries: the wait is this value to the power of the attempt, in seconds (default 3). |
| `KHADBOT_JOB_PROGRESS_INTERVAL_S` | How often a running job writes its progress and checks whether it was cancelled (default 0.5). |

Jobs are rows in the `jobs` table (state, per-pull progress, typed error) and each change sends `NOTIFY job_events` with the job's ID. The code that queues and cancels jobs is `khadbot.jobs.queue.JobQueue`; the API does not call it yet.

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
    mise run stack:smoke    # health, the report list and a night summary
    mise run stack:queue-smoke  # queue a job and check the worker finishes it
    mise run stack:logs
    mise run stack:down     # stop, keep the data volumes
    mise run stack:reset    # stop and delete the database and bucket

Everything listens on 127.0.0.1: the API on 8000, Postgres on 5432 and the SeaweedFS S3 endpoint on 8333. The data lives in named Docker volumes; the local `.khadbot_data` directory is never mounted.

### Seeding

`stack:seed` runs `scripts/data/seed_stack.py` in the image with the fixtures mounted read-only from the repository. For each report it ingests the pulls offline into the bucket, stores the report metadata, then runs the same process job the queue runs: annotate, build and store the records, project the read-model tables. A second run changes nothing.

It seeds the synthetic report `SYNTH01` and every real recorded report whose pages are present (`git lfs pull`). The real reports are large (about 200 MB of pages); for a quick seed pass the report code:

    docker compose -f deploy/compose.yaml --profile seed run --rm seed --only SYNTH01

The seed runs the job functions in-process rather than queueing them: SYNTH01 needs its own filters, encounter file and registries, which the stack's worker (built from the configuration) does not have.

### The queue smoke test

`stack:queue-smoke` runs `scripts/dev/queue_smoke.py` in the image. It queues an offline ingest of a report that is in no cache and waits for the stack's worker to fail it with the code `not_in_cache`. That exercises enqueue, the queue tables, the worker, the `jobs` row and typed failures on the real stack, with no WCL call. Ingesting a fixture report through a worker is covered by the hosted tests (`tests/integration/hosted/test_queue_pg.py`).

### CI

The `stack` job in `.github/workflows/check.yml` builds the image (nothing is pushed), starts the stack, seeds `SYNTH01`, runs the smoke script and then the queue smoke test against the stack's worker. It is the only place the image is built on every push.
