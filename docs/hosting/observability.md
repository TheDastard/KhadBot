# Logs: Loki, Alloy and Grafana

The compose file's opt-in `obs` profile collects every service's log lines into Loki and shows them in Grafana. Plain `stack:up` and the CI `stack` job start none of it. Why these tools: `docs/decisions.md` (D113). The line format is in [operations.md](operations.md#logs).

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

In Follow an ID an empty variable matches every line, so set at least one and widen the time range to the request's or job's time. The jobs table matches `request_id` through `to_jsonb(j) ->> 'request_id'`, which finds nothing (rather than failing) while `jobs` has no `request_id` column. Every set variable must match (AND).

## Alerts

`provisioning/alerting/rules.yaml` holds four rules in the `khadbot` group (KhadBot folder), evaluated every minute. The thresholds are starting values.

| Rule (uid) | Fires when | Reads |
|---|---|---|
| Worker stalled with work queued (`khadbot-worker-stalled`) | No `job.finish` line from the worker in 10 minutes while a procrastinate `todo` job has been ready (deferred or scheduled) for 10 minutes | Loki, `procrastinate_jobs`, `procrastinate_events` |
| API 5xx above 2% (`khadbot-api-5xx`) | `http.request` lines with status 500 or above are more than 2% of the API's requests over 5 minutes; no requests is OK | Loki |
| Job waiting for WCL points over 30 minutes (`khadbot-budget-wait`) | A job has been in `waiting_budget` for more than 30 minutes, measured from its `updated_at` (set when it entered the state) | `jobs` |
| LLM cost today over the daily limit (`khadbot-llm-cost-day`) | Estimated LLM cost in `usage` since 00:00 UTC is over 5 USD | `usage` |

Grafana does not expand environment variables in alert rule files, so the daily LLM limit is the threshold's `params` value in `rules.yaml`, not a setting. No contact point is provisioned: a firing rule shows in Grafana's Alerting list only.

## Docker socket on Windows

Docker Desktop (engine 29.9.3, Linux containers) on Windows 11 exposes the engine to a container that mounts `/var/run/docker.sock:/var/run/docker.sock`: a throwaway `docker:27-cli` container listed the running containers through it, and Alloy discovers the stack's containers the same way. From Git Bash, run such a `docker run -v` with `MSYS_NO_PATHCONV=1`, or Git Bash rewrites `/var/run` to a Windows path; `docker compose` reads the mount from `compose.yaml` and is unaffected. The dev loop's file tailing needs no socket.

## Open questions

- Where alerts are sent (the contact point and channel). Until that is chosen, the rules notify no one.
