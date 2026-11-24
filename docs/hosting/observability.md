# Observability

What the hosted service records about itself and where to read it:

- **Log lines**: every hosted command writes one JSON object per line to stdout, with the IDs of the request or job it serves ([Log lines](#log-lines)).
- **Collection and viewing**: the compose file's opt-in `obs` profile collects the local stack's and the dev loop's lines into Loki and shows them in Grafana, with dashboards and alert rules kept in the repo. Plain `stack:up` and the CI `stack` job start none of it. Why these tools: `docs/decisions.md` (D113).
- **Error tracking**: the API, the worker and the web app report errors through the Sentry SDKs once a DSN is set ([operations.md](operations.md#error-tracking), D116).
- **Production**: nothing ships or collects logs outside a developer's machine yet ([Production](#production)).

## Log lines

The format (D112) is defined in `src/khadbot/logging_setup.py`; [operations.md](operations.md#logs) has every field, the redaction rules and the log level and format settings. In short: base fields (`time`, `level`, `logger`, `message`, `exc`), process fields set once at start (`service`, `version` from `KHADBOT_VERSION`, `env` from `KHADBOT_ENV`), context IDs from `log_context()` (`request_id`, `job_id`, `user_id`, `report_code`, `pull_key`), and event fields passed through `extra=`, of which only the names in `EVENT_FIELDS` are written.

| Event | Written by | Fields | Level |
|---|---|---|---|
| `http.request` | `api/request_log.py`, once per API request ([operations.md](operations.md#logs)) | `method`, `route`, `status`, `duration_ms`, `error_code`; `user_id` when signed in | INFO; WARNING for 5xx; DEBUG for `/v1/health` |
| `job.start`, `job.finish` | `jobs/queue.py`, once each per run of a job ([operations.md](operations.md#logs)) | `job_kind`, `queue`, `attempt`; `job.finish` adds `outcome`, `duration_ms`, `error_code`, `points` | INFO; `job.finish` WARNING for retrying or interrupted, ERROR for failed |
| `job.settled` | `jobs/status.py`, once when an ingest whose run logged `awaiting_children` finishes | `job_kind`, `outcome`, `error_code` | INFO; ERROR for failed |
| `wcl.request` | `wcl/client.py`, once per WarcraftLogs HTTP request | `op` (the GraphQL operation name, or `oauth_token`), `status`, `duration_ms`, `attempt` | DEBUG; WARNING on failure |
| `llm.call` | `encounter/analyst/usage.py`, once per model call | `model`, `outcome`, `duration_ms`, `input_tokens`, `output_tokens` | INFO |
| `blob.error` | `storage/s3.py`, when an S3 operation fails | `op`, `status` | WARNING |

`version` is the image's `GIT_SHA` build argument; a local stack built with `GIT_SHA=$(git rev-parse --short HEAD) mise run stack:up` writes it, a plain `stack:up` leaves it out. The stack sets `env` to `stack` (`deploy/stack.env`) and the dev loop to `dev`.

## What runs

| Service | Image | Listens on | Does |
|---|---|---|---|
| `loki` | `grafana/loki:3.7.9` | 127.0.0.1:3100 | Stores the lines. One process, filesystem storage on the `lokidata` volume, structured metadata on, 7 days of retention deleted by the compactor (`deploy/observability/loki/config.yaml`). |
| `alloy` | `grafana/alloy:v1.21.3` | 127.0.0.1:12345 (its own UI) | Reads the `khadbot` compose project's containers through the Docker socket and tails `.khadbot_dev_logs/*.jsonl`, parses KhadBot's JSON lines and pushes to Loki (`deploy/observability/alloy/config.alloy`). Positions on the `alloydata` volume. |
| `grafana` | `grafana/grafana:13.3.0` | 127.0.0.1:3001 | Explore and dashboards. Anonymous users are Admin, which is acceptable only because the port is bound to 127.0.0.1; the `admin` password (`GF_SECURITY_ADMIN_PASSWORD` in `deploy/stack.env`) is for the HTTP API. State on the `grafanadata` volume. |
| `obs-readonly` | `postgres:17` | — | Runs `deploy/observability/postgres/readonly.sql` and exits: creates `khadbot_readonly` (password `KHADBOT_READONLY_PASSWORD` in `stack.env`) or resets its password, and grants SELECT on `jobs`, `usage`, `wcl_budget`, `watches` and the `procrastinate_*` tables. Idempotent. |

Grafana 3001 because the web dev server holds 3000.

Versions are the newest stable releases at least two weeks old as of 2026-11-24, from the GitHub release pages: [Loki](https://github.com/grafana/loki/releases), [Alloy](https://github.com/grafana/alloy/releases), [Grafana](https://github.com/grafana/grafana/releases).

## Starting it

`obs:up` starts the four services with `--no-deps`, so it runs beside whatever already holds Postgres:

    mise run stack:up      # or: mise run dev:infra (the dev loop)
    mise run obs:up
    mise run obs:open      # opens http://127.0.0.1:3001/explore
    mise run obs:down      # stops the four, keeps their volumes

`obs-readonly` needs the migrated tables, so start it after `stack:up` or `dev:infra` has applied migrations. `stack:down` also stops the obs services; `stack:reset` also deletes their volumes (the collected logs and Grafana's state).

With the dev loop (`mise run dev:all`), `scripts/dev/dev.py` writes each process's JSON lines to `.khadbot_dev_logs/<service>.jsonl` at the repository root (gitignored). Alloy mounts that directory read-only and tails it; the file name is the `service` fallback and `env` is `dev`. Container lines get `env` `stack`. A line's own `service` and `env` fields override both.

## Labels and structured metadata

Only `service`, `level` and `env` are Loki labels, so the number of streams stays small. Everything with many values is structured metadata: `request_id`, `job_id`, `user_id`, `report_code`, `pull_key`, `event`, `route`, `error_code`. Other JSON fields stay in the line, which is stored unchanged; parse them at query time with `| json`. Adding a label means a decision row, not just a config edit.

Postgres, SeaweedFS, Loki, Alloy and Grafana lines are not JSON-parsed: they arrive as text with their compose service as `service`. Loki itself adds `detected_level` as structured metadata; that is not a label.

## Finding lines

In Grafana, Explore, the Loki data source, code mode:

    {service=~"api|worker"} | job_id="f8c5218fb8e24980bb8cfa6d17b1d73e"
    {env="stack"} | request_id="<id from the X-Request-ID response header>"
    {service="worker", level="ERROR"} | json | line_format "{{.message}}"

Structured metadata filters go after the stream selector, with `|`. The same queries work against Loki's HTTP API:

    curl -G http://127.0.0.1:3100/loki/api/v1/query_range --data-urlencode 'query={service="worker"} | job_id="..."'

## Following an ID

One request ID (D114, D115) connects a user's failure to every line it caused:

1. The API takes the caller's `X-Request-ID` (8 to 64 letters, digits and hyphens) or generates one, and returns it in the response's `X-Request-ID`, an unhandled exception's 500 included. Its `http.request` line and every line logged while handling the request carry it, and `user_id` once the caller is known to be signed in.
2. A job queued by the request stores it in `jobs.request_id`; an ingest's process jobs inherit it.
3. The worker restores it in the job's log context, so `job.start`, `job.finish` and every line of the run (`wcl.request`, `llm.call`, library lines) carry it with the `job_id`.
4. In the web app, an error state for a server failure shows "Reference:" and the ID's first 8 characters (`apps/web/src/components/shared/request-reference.tsx`). Paste them into the `request_id` box of the Follow an ID dashboard, which matches IDs by prefix.

Work that starts outside a request generates its own ID: each live watch poll has one, carried by its lines and stored by the ingest it queues, and each backfill run has one for all its jobs (`main.py backfill` prints it). Lines with no request ID: the records cleanup and the worker's own start-up lines.

## Grafana provisioning

Everything Grafana loads comes from `deploy/observability/grafana/`, mounted read-only:

- `provisioning/datasources/datasources.yaml`: Loki (UID `loki`, the default) and Postgres as `khadbot_readonly` (UID `khadbot-pg`). Dashboards refer to these UIDs. The read-only role cannot read `wcl_tokens`, `sessions` or `oauth_states`; a table a dashboard needs is added to `readonly.sql`, since there are no default privileges for future tables.
- `provisioning/dashboards/dashboards.yaml`: loads every JSON file in `dashboards/` into the KhadBot folder. Edit a dashboard in Grafana, export its JSON and save it there.
- `provisioning/alerting/rules.yaml`: the alert rules ([Alerts](#alerts)).

`tests/unit/deploy/test_observability.py` checks these files: they parse, dashboard and rule uids are unique, every query names a provisioned data source UID, and every SQL query reads only tables `readonly.sql` grants.

## Dashboards

Four dashboards in the KhadBot folder. Loki panels filter on the `env` variable (label values, default all); Postgres panels read the one database the data source points at.

| Dashboard (uid) | Panels | Source |
|---|---|---|
| KhadBot service (`khadbot-service`) | Requests per minute by route; 5xx share; p50 and p95 `duration_ms` by route; `error_code` counts by service; a feed of WARNING and above from every KhadBot service; WCL requests and failures by `op`; LLM calls and p50/p95 duration by `model` | Loki |
| KhadBot jobs (`khadbot-jobs`) | Active jobs by state now; jobs created over time, stacked by current state; failed jobs by `error_code`; procrastinate rows per queue (todo now, todo scheduled later, doing); p50/p95 duration by `job_kind` and outcomes from `job.finish` and `job.settled`; jobs in `waiting_budget` and how long the oldest has waited | Postgres (`jobs`, `procrastinate_jobs`) and Loki |
| KhadBot spend (`khadbot-spend`) | LLM cost today (UTC); the app bucket's spent points and hourly limit now; LLM calls with no price; WCL points per hour from `usage` against the hourly limit in `wcl_budget`; LLM cost per day by model; top users and top guilds by LLM cost, then WCL points | Postgres (`usage`, `wcl_budget`) |
| KhadBot follow an ID (`khadbot-follow-id`) | Text variables `request_id`, `job_id`, `report_code`, `user_id`; every line whose IDs start with the set values, oldest first; the matching `jobs` rows (a `job_id` also finds its process jobs) | Loki and Postgres (`jobs`) |

Which log events feed which panels ([operations.md](operations.md#logs) has the fields):

- `http.request`: the service dashboard's request, 5xx and duration panels, and the API side of `error_code` counts.
- `job.finish`: job duration and outcomes, the worker side of `error_code` counts.
- `job.settled`: the final outcome of each ingest that waited for its process jobs, in the outcomes panel.
- `wcl.request`: WCL requests by `op`. Successful requests log at DEBUG, so they count only with `KHADBOT_LOG_LEVEL=DEBUG`; failures log at WARNING and always count.
- `llm.call`: LLM calls and duration by model.

In Follow an ID an empty variable matches every line, so set at least one and widen the time range to the request's or job's time. The jobs table matches `jobs.request_id` through `to_jsonb(j) ->> 'request_id'`. Every set variable must match (AND).

## Alerts

`provisioning/alerting/rules.yaml` holds four rules in the `khadbot` group (KhadBot folder), evaluated every minute. The thresholds are starting values.

| Rule (uid) | Fires when | Reads |
|---|---|---|
| Worker stalled with work queued (`khadbot-worker-stalled`) | No `job.finish` line from the worker in 10 minutes while a procrastinate `todo` job has been ready (deferred or scheduled) for 10 minutes | Loki, `procrastinate_jobs`, `procrastinate_events` |
| API 5xx above 2% (`khadbot-api-5xx`) | `http.request` lines with status 500 or above are more than 2% of the API's requests over 5 minutes; no requests is OK | Loki |
| Job waiting for WCL points over 30 minutes (`khadbot-budget-wait`) | A job has been in `waiting_budget` for more than 30 minutes, measured from its `updated_at` (set when it entered the state) | `jobs` |
| LLM cost today over the daily limit (`khadbot-llm-cost-day`) | Estimated LLM cost in `usage` since 00:00 UTC is over 5 USD | `usage` |

Grafana does not expand environment variables in alert rule files, so the daily LLM limit is the threshold's `params` value in `rules.yaml`, not a setting. No contact point is provisioned: a firing rule shows in Grafana's Alerting list only.

## Error tracking

With `SENTRY_DSN` set, the API and the worker send unhandled exceptions and `ERROR` records to Sentry or GlitchTip, redacted like the log lines and tagged with the same IDs, so an event's `request_id` or `job_id` tag finds its lines here; `NEXT_PUBLIC_SENTRY_DSN` does the same for the web app's client-side errors. Without a DSN nothing is imported or sent. What is collected and how: [operations.md](operations.md#error-tracking). No DSN is set anywhere yet, since the service is not chosen (D116).

## Production

Only the local stack and the dev loop are collected. Staging and production logs go to the platform's stdout and are not shipped, kept or shown in Grafana: that waits on the hosting platform and on choosing Grafana Cloud or a Loki run ourselves. The plan is the same Alloy config with a remote Loki endpoint (or the platform's log drain), the same dashboard files, and the Postgres data source over TLS with the read-only role.

## Docker socket on Windows

Docker Desktop (engine 29.9.3, Linux containers) on Windows 11 exposes the engine to a container that mounts `/var/run/docker.sock:/var/run/docker.sock`: a throwaway `docker:27-cli` container listed the running containers through it, and Alloy discovers the stack's containers the same way. From Git Bash, run such a `docker run -v` with `MSYS_NO_PATHCONV=1`, or Git Bash rewrites `/var/run` to a Windows path; `docker compose` reads the mount from `compose.yaml` and is unaffected. The dev loop's file tailing needs no socket.

## Open questions

Decisions deferred until a hosting platform is chosen. Each has its work item in `docs/follow-ups.md`, named after the arrow.

- Which platform hosts the API, the worker and the web app. Every item below waits on it. → "No hosting platform chosen"
- Where production logs go, Grafana Cloud or a Loki run ourselves (D113), and how they get there: the platform's log drain or an Alloy sidecar. → "Hosted logs aren't collected"
- How long hosted logs are kept; they hold WCL user IDs and report codes. → "Hosted log retention isn't set"
- Who signs in to a hosted Grafana, and how. → "No hosted Grafana access model"
- Whether dashboard queries read a replica rather than the primary database. → "The read-only Postgres role exists only on the local stack"
- Which error tracker takes the DSNs: Sentry's hosted plan or GlitchTip run ourselves (D116). → "Error tracker service not chosen"
- Where alerts are sent. Until a channel is chosen, the rules notify no one. → "Alerts have no contact point"
- How worker health is checked once more than one machine runs workers: the health file is local to each. → "Worker health is per machine"

Once these are settled, the deploy sets `KHADBOT_ENV` and `KHADBOT_VERSION` for the API and worker, and `NEXT_PUBLIC_KHADBOT_ENV`, `NEXT_PUBLIC_KHADBOT_VERSION` and `NEXT_PUBLIC_SENTRY_DSN` for the web build. → "Hosted deploys set neither `KHADBOT_ENV` nor `KHADBOT_VERSION`", "Not deployed"
