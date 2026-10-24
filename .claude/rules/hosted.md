---
paths:
  - "src/khadbot/db/**"
  - "src/khadbot/jobs/**"
  - "src/khadbot/auth/**"
  - "src/khadbot/storage/**"
  - "src/khadbot/wcl/**"
  - "src/khadbot/wiring.py"
  - "deploy/**"
---

# Hosted mode

Hosted mode (`KHADBOT_MODE=hosted`) runs the same engine behind Postgres, an S3-compatible bucket and a job queue. Local mode must keep working with none of them.

## Secrets

- Secrets (WCL client secret, user and refresh tokens, database URLs, bucket credentials, encryption keys) never appear in code, logs, job payloads, LLM context, findings, test fixtures or error messages. Payloads carry a user ID, never a token. Tests use obviously fake values.
- Don't log request headers, token responses or connection strings. Log IDs.

## Database

- Schema changes are new numbered SQL files in `src/khadbot/db/migrations/`, applied by `python main.py migrate`. Never edit an applied file: its checksum is recorded.
- SQL is plain, parameterised SQL through `psycopg`; no string-built queries and no ORM.
- Write operations that a job may repeat are idempotent.

## API

- Every report route checks that the caller may read the report. A report the caller cannot read answers 404, never 403, so the API never confirms that a private report exists.
- Report routes get the caller from `auth.routes.optional_user` or `current_user` and check with `ReportAccess.require_readable` (`wiring.report_access()`); never write a second access check.
- State-changing routes depend on `auth.routes.require_csrf` (the `X-KhadBot-CSRF: 1` header plus an `Origin` check). Cookies stay HttpOnly and SameSite=Lax, and Secure everywhere but plain-HTTP localhost.

## Dependencies and layers

- Hosted libraries (`psycopg`, `psycopg_pool`, `boto3`, `zstandard`, the queue and crypto libraries) live in the `hosted` group and are imported only on the hosted path that needs them. A missing group raises an error that names it (`errors.missing_group_message`).
- Only `khadbot/wiring.py` and the entry points read the mode. Code below them takes a store as an argument. The layer contract in `pyproject.toml` enforces the imports.
- Unit tests use no Postgres, S3 or Docker. Tests that need them live in `tests/integration/hosted/` under the `hosted` marker.

## Containers (`deploy/`)

- The image is built from the repository root with `deploy/Dockerfile`. `.dockerignore` excludes everything by default, so a new file reaches the image only through a `COPY` line and an allow entry; never add `.env`, `.khadbot_data*` or `.git`.
- No secret is baked into an image or written in a compose file. `deploy/stack.env` holds throwaway local values only (the local S3 store is SeaweedFS; production uses Cloudflare R2); real deployments set the same variable names on the platform.
- The stack never mounts `.khadbot_data`; hosted mode keeps its data in Postgres and the bucket. The image runs as a non-root user.
- A new environment variable goes in `.env.example`, in `docs/hosting.md` under its component, and in `stack.env` when the local stack needs it.
