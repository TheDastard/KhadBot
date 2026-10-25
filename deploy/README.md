# deploy/

Container and platform files for hosted mode.

| File | What it is |
|---|---|
| `Dockerfile` | The one image: API, migrations and the worker. Build from the repository root. |
| `compose.yaml` | The local stack: Postgres, SeaweedFS (the local S3 store), a bucket-creation step, migrate, API and the job queue worker, with the seed and the queue smoke test behind profiles. |
| `stack.env` | Throwaway environment for the local stack. Not secrets; never put real values here. |

Run it with `mise run stack:up`, `stack:seed`, `stack:smoke` and `stack:queue-smoke`. The environment variables, the seed and the image are described in [docs/hosting.md](../docs/hosting.md).

`.dockerignore` at the repository root keeps everything out of the build context except the files the image copies. Do not mount `.khadbot_data` or copy `.env` into a container.
