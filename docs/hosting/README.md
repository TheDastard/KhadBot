# Hosting

How the hosted service is packaged and configured: the container image, the environment variables each component reads, and the local stack that runs them. Local mode (the CLI on your machine) needs none of this. The reasons behind these choices are in `docs/decisions.md` (container image and local stack).

| File | Covers |
|---|---|
| [configuration.md](configuration.md) | Every environment variable by component (API, migrate, worker, watch, lookup, search, replay, seed) |
| [local.md](local.md) | The local container stack, the native dev loop, the web app against either, test sessions |
| [operations.md](operations.md) | Signing out everywhere, rotating the token key, retention and backfill, the usage ledger, logs, worker health |
| [observability.md](observability.md) | Logs, request IDs and error tracking in one place: the log events, following an ID from the API to the worker, Loki, Alloy and Grafana in the stack's `obs` profile, dashboards, alerts, production status |

A new environment variable goes in `configuration.md` under its component (`.claude/rules/hosted.md`).

## The image

One image holds every role. `deploy/Dockerfile` builds it from the repository root:

    docker build -f deploy/Dockerfile -t khadbot .

- Built with uv from `uv.lock` on the Python version the base image provides (`.python-version` names it), installing only the `api` and `hosted` groups. No CLI, LLM provider packages or dev tools.
- Carries `src/`, `config/` (the authored encounter files and registries: every cache key hashes them, so the image and the stored data must agree on them), `main.py` and the seed script.
- Runs as the non-root user `khadbot` (uid 10001). `KHADBOT_DATA_DIR` is scratch space inside the container; hosted mode keeps its data in Postgres and the bucket.
- The command picks the role: `api` (the default, port 8000), `migrate` (apply migrations and exit), `worker` (the job queue worker).
- `.dockerignore` excludes everything except the files the image copies, so `.env` and `.khadbot_data*` can never enter the build context.

Image size: not measured yet. The `stack` CI job prints it (`docker image ls khadbot:local`); record the figure here once a build has run.
