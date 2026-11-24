# Running the hosted stack locally

## The local stack

Needs Docker with Compose. From the repository root:

    mise run stack:up       # build the image; start Postgres, SeaweedFS, the migrate step and the API
    mise run stack:seed     # load the recorded fixture reports (offline, no WCL calls)
    mise run stack:smoke    # health, a signed-out 404, then the report list and a night summary as the smoke test user
    mise run stack:queue-smoke  # queue a job and check the worker finishes it
    mise run stack:logs
    mise run stack:down     # stop, keep the data volumes
    mise run stack:reset    # stop and delete the database, bucket and collected logs

Everything listens on 127.0.0.1: the API on 8000, Postgres on 5432 and the SeaweedFS S3 endpoint on 8333. The data lives in named Docker volumes; the local `.khadbot_data` directory is never mounted.

To search the stack's logs in Grafana, add `mise run obs:up`; see [observability.md](observability.md).

### Seeding

The seed also provisions the test user `smoke` (see [Test sessions](#test-sessions)) with each seeded report cached as readable for it and as not public, so `stack:smoke` can read them without a Warcraft Logs sign-in and a signed-out visitor gets 404 without asking WCL. The readable answers stand for an hour (`KHADBOT_ACCESS_RECHECK_S`) and the not-public ones for 5 minutes (`KHADBOT_ACCESS_DENIED_RECHECK_S`); the stack's placeholder WCL credentials cannot renew either, so a visitor's request after that gets 503, and the routes answer 503 for everyone once the hour is up. Seed again to reset them.

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

**What local mode serves.** The read routes (`/v1/reports`, `/v1/reports/{code}`, `/night`, the pull `digest`, `timeline`, `events` and `findings`, and `/v1/evidence/{ref}`) over the data directory, and `GET /v1/health`. Findings are the cached ones; nothing runs the analyst or calls Warcraft Logs. There is no sign-in, so every request may read every ingested report. CORS allows `http://localhost:3000` and `http://127.0.0.1:3000` for `GET`, without credentials. The hosted-only routes (`/v1/auth/*`, `/v1/me`, `/v1/jobs/*`, `/v1/reports/{code}/watch`, `/v1/reports/{code}/pulls/{fight}/review`, `/v1/budget`) are not mounted and answer 404 like any unknown route.

**What `/v1/health` reports.** `mode` is `local` or `hosted`, given by the entry point: `main.py serve` says `local`, `main.py api` in hosted mode says `hosted`. It is a label only. `features` has one boolean per optional area, true only when its routes are mounted, and clients branch on `features` only (`.claude/rules/web.md`): with `sign_in` off the web app shows the reports without a session and labels the header "Local", and a direct link to an area whose flag is off shows "Not available on this server yet".

    serve   {"mode": "local",  "features": {"sign_in": false, "ingest_jobs": false, "live_watch": false, "budget": false, "reviews": false, "chat": false}}
    api     {"mode": "hosted", "features": {"sign_in": true,  "ingest_jobs": true,  "live_watch": true,  "budget": true,  "reviews": true,  "chat": false}}

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

The full experience runs hosted mode on your machine: Postgres and the bucket in Docker, the API and the worker natively, so a Python edit takes effect in seconds and no image is built. It runs the code production runs, against a real database and bucket. Needs Docker Desktop running (`scripts/dev/setup.ps1` checks it and pulls the images; `-WithDocker` or `-Full` installs it) and `uv sync --extra dev` (the reload uses `watchfiles`).

    mise run dev:all        # Postgres and SeaweedFS in Docker, the bucket, migrations, then the API and the worker
    mise run dev:seed -- --only SYNTH01   # in a second terminal; without --only, every recorded report
    mise run dev:smoke      # health, a signed-out 404, a night summary, and a queued job the worker finishes

`dev:all` prints `dev: ready at http://127.0.0.1:8000` when the API answers and keeps running; the API's, the worker's and the web dev server's logs share the terminal with `[api]`, `[worker]` and `[web]` prefixes. Ctrl-C stops them all. The pieces run on their own too: `dev:infra` (the Docker services, the bucket and migrations, then exits), `dev:api` and `dev:worker` (each in the foreground, with reload) and `dev:web` (the web dev server). `dev:all -- --no-web` leaves the web dev server out, and it is left out with a note when the workspace's packages aren't installed (`mise run install:web`).

**Settings.** `deploy/dev.env` (gitignored; `setup.ps1` creates it from `deploy/dev.env.example`, or copy it by hand) holds the same throwaway values as `deploy/stack.env` with `127.0.0.1` in place of the container host names. Use `127.0.0.1`, not `localhost`, for Postgres and the bucket: on Windows the connection pool stalls on its IPv6 attempt for `localhost`. The runner lays dev.env over the main checkout's `.env` (found the way the app finds it, so a worktree needs none of its own), which still supplies the WCL credentials, `LLM_PROVIDER` and the rest. With no WCL credentials anywhere, placeholders let the API start; everything but signing in to Warcraft Logs then works. The worker asks WCL for an app token when it starts (no points); a worker started without real credentials logs a warning that it could not read WCL's rate limit and carries on.

**Reload.** `main.py api --reload` and `main.py worker --reload` (what the tasks run) watch `src/khadbot` and `config/` for Python and YAML changes and restart the command; each prints `[reload] <file> changed; restarting`. A restart takes about 3 seconds. It stops the old process hard (on Windows a signal cannot ask it to finish), so a job the worker was running stays in its running state: cancel it, or `mise run stack:reset`. `--reload` needs the `dev` extra and is for development only; containers never use it.

**Sign in.** Real Warcraft Logs sign-in needs your own client in `.env` and a browser: the API runs on `localhost:8000`, whose callback (`KHADBOT_WCL_REDIRECT_URI` in dev.env) is registered with Warcraft Logs. This has not been exercised on the dev loop yet. Without a browser, the seed provisions the smoke user whose cookie `khadbot_session=local-stack-smoke-session` reads the seeded reports (see "Seeding"). Test sessions, which replace this for the web app, come with their own task.

**Port 8000 is shared.** `mise run stack:up` and `dev:all` both want it, and the dev tasks refuse to start while something listens there. Run `mise run stack:down` before the dev loop, or the other way round. They share the same Postgres and bucket volumes (the infrastructure is the stack's `postgres` and `seaweedfs` services, in the same compose project), so seeded data carries over, and `stack:down` also stops the dev loop's containers.

**Reset.** `mise run stack:reset` stops everything in the project and deletes the database and the bucket; `dev:all` recreates them, and `dev:seed` fills them again.

**Switches for development.** WCL replay answers WCL from the recorded fixture reports, so ingest and live watch spend no WCL points: uncomment `KHADBOT_WCL_REPLAY` in `deploy/dev.env` (see "WCL replay" under "Environment variables" above). The fake model provider answers reviews with no model key: set `LLM_PROVIDER=fake` (see "Fake model provider" under "Environment variables"). Test sessions are described under "Test sessions" below.

**Time to start.** With the images pulled and the volumes in place, `dev:all` has the infrastructure ready in about 3 seconds and the API healthy about 8 seconds after the command (measured on the maintainer's Windows machine, 2026-10-31). The first run also pulls the Postgres and SeaweedFS images, which takes around a minute depending on the connection.

### The web app

The dashboard (`apps/web`, `.claude/rules/web.md`) is a Next.js app served by its dev server on `http://localhost:3000`. `NEXT_PUBLIC_API_BASE` names the API it talks to, with no default in code: `dev:all` and `dev:web` set `http://localhost:8000`, the dev loop's API, and `mise run web:dev` defaults to the same. Use `localhost`, not `127.0.0.1`, for the API: the session cookie (from sign-in or a test session) is set for `localhost`, and the API's `KHADBOT_WEB_ORIGINS` in `deploy/dev.env` lists `http://localhost:3000`.

Against local mode, point it at `main.py serve` instead (`NEXT_PUBLIC_API_BASE=http://127.0.0.1:8000 mise run web:dev` after `mise run serve:fixtures`): it reads `/v1/health`, finds sign-in off, and labels the header "Local". It sends no cookie to such a server, which allows only uncredentialed `GET`s from `http://localhost:3000` and `http://127.0.0.1:3000`.

The browser loads one third-party script, Wowhead's tooltips (`docs/game-art.md`); everything else comes from the API. Fonts are bundled from npm, so neither the dev server nor a build reaches Google Fonts.

**End-to-end tests.** Playwright drives Chromium (`mise run install:playwright` once, or `setup.ps1 -WithPlaywright` or `-Full`). Two suites:

1. Hosted, on the dev loop with WCL replay and test sessions:

        # deploy/dev.env: uncomment KHADBOT_WCL_REPLAY=tests/fixtures/encounter/wcl
        # deploy/dev.env: LLM_PROVIDER=fake, FAKE_LLM_DELAY_SECONDS=1.5 (review.spec.ts runs reviews on the fake model)
        mise run dev:all                        # the API, worker and web dev server
        mise run dev:seed -- --only SYNTH01     # second terminal
        mise run test:e2e

   Its setup signs in the test users `e2e-alice`, `e2e-bob` and `e2e-carol` with `test-session --storage-state` (files in `apps/web/e2e/.auth/`, gitignored: they hold live cookies), each able to read the seeded reports. `open-pull.spec.ts` opens real pulls through replay and so needs `E2E_REPORTS=SYNTH01,H7z1fqDXjRTFnbtg` with the second report readable but not seeded, on a stack where its pulls are not loaded yet (`mise run stack:reset`); without that those three tests skip and its mocked state screens still run. `open-pull-signed-out.spec.ts` opens a pull of the public `QkVa6GHRK8yfrTbw` signed out (a fight replay has pages for; it skips once each of those is loaded), and the Raids spec looks the same report up. `timeline.spec.ts` needs `H7z1fqDXjRTFnbtg` in `E2E_REPORTS` and skips without it, as the long-fixture tests of `pull-page.spec.ts` do. It runs in two passes (`apps/web/e2e/run.mjs`): the functional tests on `E2E_WORKERS` parallel workers (default 4; the web CI workflow sets 2), then the tests that measure latency (tagged `@budget`) on a single worker so load from other tests cannot skew them. Every worker shares one web dev server and one API, so Playwright's own default (half the logical CPUs) overloads them and tests time out; raise `E2E_WORKERS` only on a machine that stays green with it. Before the tests, the setup opens each route once so `next dev` has compiled it. Put a report that tests look up as public outside `E2E_REPORTS`: the setup marks every listed report not public. It reuses the web dev server on :3000, or starts one. No test reaches a host other than this machine: the fixture aborts any other request.

2. Local mode: `mise run test:e2e:local` starts `main.py serve` on `127.0.0.1:8001` over `.khadbot_fixture_data` (`mise run gen:fixture-data` once; its setup uploads generated test portraits into that directory's `art/`) and its own web dev server on :3000 pointed at it, so stop any other web dev server first (`dev:all -- --no-web` keeps the rest of the dev loop).

The `web` CI workflow (`.github/workflows/web.yml`) runs `check:web` on Linux and Windows, and both suites on Linux: the dev loop with SYNTH01, then the local suite over `QkVa6GHRK8yfrTbw` only. The job checks out without LFS and pulls `QkVa6GHRK8yfrTbw/**` (about 10 MB) and every `*/report.json` (about 3 MB: the guild and character searches list nights from each report's metadata); specs that need another report's pages skip.

## Test sessions

Playwright, offline work and multi-user checks need signed-in users without a Warcraft Logs login. `khadbot test-session` starts a session for a named test user:

    mise run dev:session -- --user alice --reports ABC123       # on the dev loop (loads deploy/dev.env); prints the cookie value on stdout, nothing else
    mise run dev:session -- --user bob --storage-state .auth/bob.json   # a Playwright storage-state file instead
    docker compose -f deploy/compose.yaml run --rm --no-deps -T api test-session --user alice   # inside the stack

| Option | Meaning |
|---|---|
| `--user NAME` | The test user, created on first use and reused after: 1 to 32 lowercase letters, digits or hyphens (default `dev`). Use several names to test access rules and "another user sees the same findings". |
| `--reports CODE...` | Reports this user may read. Each is cached as readable for the user and as not public, so every other user and any visitor gets 404 without a Warcraft Logs call. The answers stand for `KHADBOT_ACCESS_RECHECK_S` and `KHADBOT_ACCESS_DENIED_RECHECK_S`; run the command again to renew them. |
| `--hours N` | Session lifetime (default 24, at most 168). |
| `--storage-state FILE` | Write a Playwright `storageState` file holding the session cookie, and print nothing on stdout. The file is a live credential: keep it out of the repository. |
| `--domain HOST` | The cookie's domain in that file (default `localhost`; browsers share it across ports, so it serves the web app and the API). |
| `--quiet` | No summary on stderr. |

To act as a user with `curl`: `curl -b "khadbot_session=$(uv run python main.py test-session --user alice --quiet)" http://localhost:8000/v1/me`.

It is a development switch and refuses to run unless `KHADBOT_TEST_SESSIONS` is `1`, `true`, `yes` or `on`, before it opens a database. The local stack's env file (`deploy/stack.env`), the dev loop's (`deploy/dev.env.example`, copied to `deploy/dev.env`) and CI turn it on; staging only for the load test; production never. The API and the worker log a warning at start while it is on. A test user's session signs nobody in while the switch is off, so sessions issued during a load test stop working once the API restarts without it. It also needs the hosted settings (`KHADBOT_MODE=hosted`, `DATABASE_URL`); `mise run dev:session` runs it through `scripts/dev/dev.py` with the dev loop's environment, so it reaches the stack's Postgres on `127.0.0.1:5432` from the host.

Test users have a negative WCL user ID (-1 down to -(2^40), derived from the name). Warcraft Logs account IDs are positive, and sign-in refuses a non-positive ID, so a real account can never be a test user. A test user holds no Warcraft Logs tokens, so its jobs use the app's auth (which WCL replay answers). The session cookie is never logged and only its SHA-256 is stored. The smoke tests (`stack:smoke` in the image, `dev:smoke` natively) start their own session this way for the user `smoke`; the old fixed `KHADBOT_STACK_SMOKE_SESSION` value is gone.
