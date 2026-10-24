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

## Dependencies and layers

- Hosted libraries (`psycopg`, `psycopg_pool`, `boto3`, `zstandard`, the queue and crypto libraries) live in the `hosted` group and are imported only on the hosted path that needs them. A missing group raises an error that names it (`errors.missing_group_message`).
- Only `khadbot/wiring.py` and the entry points read the mode. Code below them takes a store as an argument. The layer contract in `pyproject.toml` enforces the imports.
- Unit tests use no Postgres, S3 or Docker. Tests that need them live in `tests/integration/hosted/` under the `hosted` marker.
