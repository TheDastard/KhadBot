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

## Docker socket on Windows

Docker Desktop (engine 29.9.3, Linux containers) on Windows 11 exposes the engine to a container that mounts `/var/run/docker.sock:/var/run/docker.sock`: a throwaway `docker:27-cli` container listed the running containers through it, and Alloy discovers the stack's containers the same way. From Git Bash, run such a `docker run -v` with `MSYS_NO_PATHCONV=1`, or Git Bash rewrites `/var/run` to a Windows path; `docker compose` reads the mount from `compose.yaml` and is unaffected. The dev loop's file tailing needs no socket.
