# Configuration

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

In hosted mode the API also reads the sign-in settings (`WARCRAFTLOGS_CLIENT_ID`, `WARCRAFTLOGS_CLIENT_SECRET`, `KHADBOT_TOKEN_KEYS`, `KHADBOT_WCL_REDIRECT_URI`, `KHADBOT_POST_LOGIN_URL`, `KHADBOT_WEB_ORIGINS` and the session and access settings in `.env.example`) and refuses to start without the client credentials and a token key, or with the example token key from `deploy/stack.env` (it is public) when the redirect URI is not plain-HTTP localhost. It calls WCL only to check whether a user may read a report (cached in `report_access`) and to sign users in; it never fetches events. An access check that isn't cached costs a WCL request, so it first reserves 1 point from the WCL budget as the caller (a signed-out caller against the visitors' shared allowance); with none free the route answers 429 `budget_exhausted` with `wait_s` and `Retry-After`, without asking WCL. It queues ingests on the job queue and never runs them.

| Variable | Meaning |
|---|---|
| `KHADBOT_WEB_ORIGINS` | The web app's origins. CORS allows exactly these, with credentials, `GET`, `POST`, `DELETE` and the `Content-Type`, `X-KhadBot-CSRF` and `Last-Event-ID` headers; state-changing routes also refuse a browser `Origin` outside this list. The API refuses to start with an entry that is not an origin (`*` included) and, when the redirect URI is not plain-HTTP localhost, with an empty list or a non-https origin. |
| `KHADBOT_SSE_KEEPALIVE_S` | How often an idle job event stream sends a comment line and re-reads the job (default 15). Keep it under your proxy's idle timeout. |
| `KHADBOT_MAX_BODY_BYTES` | The largest request body the API reads (default 65536); a larger one answers 413 `body_too_large`. 0 accepts any. |
| `KHADBOT_RATE_LIMIT_PER_MIN` | Requests one client address may make per minute; more answer 429 `rate_limited` with `Retry-After`. `GET /v1/health` and CORS preflights are not counted. Unset, it is 300 when `KHADBOT_WCL_REDIRECT_URI` is not plain-HTTP localhost and off on localhost (the dev loop and the end-to-end tests); 0 turns it off. The address is the connection's peer, so behind a proxy set uvicorn's `FORWARDED_ALLOW_IPS` to the proxy's addresses, or every client shares the proxy's count. Each API process counts on its own. |
| `KHADBOT_VISITOR_MAX_JOBS` | Ingests opened by signed-out visitors that may be queued, running or waiting for points at once, all visitors together (default 20). The next visitor's new pull answers 429 `visitor_queue_full`; a pull that is already loading can still be followed, and signing in lifts the limit. |
| `KHADBOT_SSE_MAX_STREAMS` | Job event streams one API process keeps open at once (default 500). The next one answers 503 `too_many_streams`; the job can still be read with `GET /v1/jobs/{id}`. Every stream of a process shares one Postgres `LISTEN` connection, so streams cost no database connections. |

The routes: every report, pull, timeline, events, findings and evidence route answers 404 `report_not_found` unless the caller (signed in or not) can read the report; `GET /v1/reports` lists the signed-in user's own reports; `POST /v1/reports` opens pulls (or a whole report): it answers `ready` with no job when every pull asked for is already loaded, queues an ingest, or returns the queued or running ingest that already covers the request, whoever started it. A signed-out visitor may open one pull at a time of a report they can read (a public one): it loads on the app's token and counts against the visitors' shared allowance (`KHADBOT_BUDGET_VISITOR_SHARE`); several pulls or a whole report need sign-in (401 `not_signed_in`). `GET /v1/jobs/{id}` and `GET /v1/jobs/{id}/events` (server-sent events) follow a job, with one step list (`download`, `prepare`, `analyse`) per pull, for anyone who can read its report, signed in or not; `POST /v1/jobs/{id}/cancel` stops it, for the user who started it only (403 `not_your_job` for anyone else; a visitor's job has no owner and runs to the end). On Windows the hosted API runs on a selector event loop (psycopg's async connections need it). A proxy in front of the API must not buffer `text/event-stream` responses (the API sends `X-Accel-Buffering: no`).

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
| `KHADBOT_WCL_LIMIT_SCHEME` | `current` (default) or `new`: how WCL charges the account that owns the client (`docs/wcl/access-and-points.md`, "Rate limits and point types"). Until the first `rateLimitData` read the bucket assumes the scheme's documented limit, 3,600 or 1,000 points an hour; after it, WCL's own limit, so a subscription shows up without a deploy. |
| `KHADBOT_BUDGET_USER_TOKENS_SPEND_APP` | `true` (default): a job on a signed-in user's token counts in full against the app's points, as the budget probe measured. `false` needs the `new` scheme and only changes anything once `KHADBOT_BUDGET_USER_POINTS_PER_HOUR` is set. |
| `KHADBOT_BUDGET_GUILD_SHARE`, `KHADBOT_BUDGET_USER_SHARE` | Share of the hourly points one WCL guild and one user may use per window (defaults 0.25 and 0.10), user-token jobs included. |
| `KHADBOT_BUDGET_VISITOR_SHARE` | Share of the hourly points every signed-out visitor together may use per window (default 0.10). A visitor opens one pull of a public log at a time, on the app's token; all visitors' opens count against this one allowance. |
| `KHADBOT_BUDGET_HEADROOM_SHARE` | Share kept free for live raid ingest (default 0.20). |
| `KHADBOT_BUDGET_POINTS_PER_PULL`, `KHADBOT_BUDGET_POINTS_PER_SINGLE_PULL`, `KHADBOT_BUDGET_POINTS_PER_JOB` | The estimate a job reserves: 7.2 per batched pull (a 25-pull night is about 180 points after cross-fight batching), 14 for a pull fetched alone, plus 4 for the report metadata and rate-limit reads. |
| `KHADBOT_BUDGET_DEFAULT_PULLS` | Pulls assumed for a whole-report ingest whose metadata isn't cached yet (default 25). |
| `KHADBOT_BUDGET_DATABASE_POINTS_PER_HOUR` | The `new` scheme's database points (default 250). Ingest uses none. |
| `KHADBOT_BUDGET_USER_POINTS_PER_HOUR` | A signed-in user's own pool on the `new` scheme; unset until it is measured. |
| `KHADBOT_BUDGET_RESERVATION_TTL_S` | A reservation no job settled (a worker died) is dropped after this long (default 7200). |

Jobs are rows in the `jobs` table (state, per-pull progress, typed error) and each change sends `NOTIFY job_events` with the job's ID. The code that queues and cancels jobs is `khadbot.jobs.queue.JobQueue`; the API calls it for the ingest routes.

### Pull reviews

A signed-in user can review a pull (`POST /v1/reports/{code}/pulls/{fight}/review`; `GET` on the same path says what a review would do). The API answers from the findings cache when the findings for this data, prompts, analyst model and depth exist: they are shared by everyone who can read the report, cost no quota and make no job. Otherwise it queues a `review` job (the worker's `review` queue, which needs the LLM settings above) and the user follows it with `GET /v1/jobs/{id}`; the findings appear on the findings route. A review that someone has already queued or is running is returned to a second asker instead of a second job. The API names the analyst model from the configuration (`ANALYST_MODEL`, else the provider's model setting) without building a client; the job carries it and fails with `model_mismatch` before any model call when the worker is configured with another one, so keep the API and the worker on the same settings. Every model call of a review writes a `usage` row (`operations.md`).

| Variable | Meaning |
|---|---|
| `KHADBOT_REVIEWS_PER_DAY` | Reviews one user may start in any rolling 24 hours (default 10); the next answers 429 `quota_exceeded` with the quota and `Retry-After`. Queued, running and done reviews count; failed and cancelled ones do not. 0 allows none; cached findings stay readable. |

### Live report watch

A signed-in user can watch a report that is still being logged (`POST /v1/reports/{code}/watch`; `GET` shows the watch and `DELETE` stops it; `GET /v1/watches` lists the caller's active watches, newest first). The watch polls the report's metadata on the user's own WCL token and queues an ingest of each new boss fight (`docs/decisions.md`, D64). It runs on the `watch` queue, which workers take jobs from by default; a worker started with `--queues` or `KHADBOT_WORKER_QUEUES` must list `watch` to run polls. Settings, all optional:

| Variable | Meaning |
|---|---|
| `KHADBOT_WATCH_POLL_INTERVAL_S` | Seconds between metadata reads of a watched report (default 180). |
| `KHADBOT_WATCH_QUIET_PERIOD_S` | A watch stops when no new boss fight turned up for this long (default 2700). |
| `KHADBOT_WATCH_MAX_LENGTH_S` | A watch stops after this long in all (default 21600). |
| `KHADBOT_WATCH_POLL_POINTS` | Points one poll reserves from the WCL budget (default 1; a poll costs 2.01 points for a raid night, `docs/wcl/costs.md`, "Live watch polls"). Never below 1. |
| `KHADBOT_WATCH_MAX_FAILURES` | A watch stops after this many failed polls in a row (default 5). |

Polls and the ingests a watch queues are live requests to the WCL budget, which may use the headroom (`KHADBOT_BUDGET_HEADROOM_SHARE`). A poll the budget has no points for is skipped; the watch shows it in `polls_skipped`. Watches are rows in the `watches` table (migration 0007), one active per report.

### Report lookup

`GET /v1/lookup/report?source=<code or URL>` lists a report's boss pulls from WCL's report metadata before anything is loaded (`features.report_lookup`). It reads the metadata ingest uses, through the raw page cache, after the access check: unreadable or missing reports answer 404 `report_not_found`. A caller with a WCL sign-in fetches on their own token, anyone else on the app's. A fetch reserves points from the WCL budget; with none free the route answers 429 `budget_exhausted` with `wait_s` and `Retry-After` and queues nothing.

| Variable | Meaning |
|---|---|
| `KHADBOT_LOOKUP_META_MAX_AGE_S` | How long cached metadata of a log that may still be live stands before the next lookup fetches it again (default 120). |
| `KHADBOT_LOOKUP_SETTLED_AFTER_S` | Metadata fetched at least this long after the log's last fight is of a finished log and stands for good (default 21600). |
| `KHADBOT_LOOKUP_POINTS` | Points one lookup reserves from the WCL budget (default 2, measured at 2.01 for a raid night; never below 1; `docs/wcl/report-data.md`, "Report metadata"). |

### Guild, character and server search

`GET /v1/servers`, `GET /v1/search/guild?region=&server=&name=` (or `?url=` a warcraftlogs.com guild URL) and `GET /v1/search/character?region=&server=&name=&encounter=` (`features.raid_search`, `api/raids/`). The server list (US and EU) is `config/registries/servers.yaml`, loaded at start-up (a malformed file stops the API) and served whole in one response with a day's `Cache-Control` and an ETag; the API never asks WCL for it, and a search naming a server not in it answers 404 `server_unknown` without spending a point. Rebuild the file with `uv run python scripts/data/build_server_list.py` (from the committed recording; `--live` asks WCL, after a typed `yes`). A guild search lists the guild's reports from the last 30 days, a character search the character's recent reports; each listed night is built like a report lookup (same metadata, same raw page cache), at most `KHADBOT_SEARCH_MAX_REPORTS` of them. Listing a guild's or character's reports spends WCL **database points**, which the budget holds against their own cap. A guild's or character's identity is kept in the blob store for good (`search/guilds/`, `search/characters/`); a listing made on the app token holds public reports only and is shared by every caller without a WCL sign-in, in the blob store (`search/listings/`); a listing made on a user's own token stays in memory for that user only. Every listed report passes the access check on every read; one the caller can't read is left out (`left_out`). Errors: 400 `bad_guild_url`, 404 `server_unknown`, `guild_not_found`, `character_not_found`, 429 `budget_exhausted`, 503 `wcl_unavailable`.

| Variable | Meaning |
|---|---|
| `KHADBOT_SEARCH_GUILD_WINDOW_DAYS` | How far back a guild search looks (default 30). |
| `KHADBOT_SEARCH_MAX_REPORTS` | The most reports one search lists (default 15, at most 100); each costs a metadata request when it isn't cached. |
| `KHADBOT_SEARCH_LISTING_MAX_AGE_S` | How long a guild or character listing is reused: the shared public one, or a user's own (default 900). |
| `KHADBOT_SEARCH_NOT_FOUND_MAX_AGE_S` | How long a "no such guild or character" answer stands (default 3600). |
| `KHADBOT_SEARCH_POINTS` | Points each search request reserves: a guild or character lookup, a listing (default 1.2, never below 1; `docs/wcl/search.md`, "Queries and cost"). |
| `KHADBOT_SEARCH_DATABASE_POINTS` | Database points a report listing reserves (default 1, measured at 1). |
| `KHADBOT_ACCESS_PUBLIC_RECHECK_S` | How long a cached "public" report-access answer stands (default 21600), longer than `KHADBOT_ACCESS_RECHECK_S` (a user-token answer, 3600) and `KHADBOT_ACCESS_DENIED_RECHECK_S` (300). A log its owner makes private stays readable here for up to this long. |

### WCL replay

A development switch for the API, the worker and the CLI (`docs/decisions.md`, D71): with `KHADBOT_WCL_REPLAY` set to a directory of recorded reports, every WCL data request is answered from that directory or refused, and none reaches the network. Ingest, the queue, the budget, progress, live watch and access checks run their production code; only the answers are recorded ones. Sign-in still goes to real WCL: the code exchange, the refresh and the current-user query pass through, so real sign-in keeps working, and a signed-in user's ingests and watches are then replayed too. Off unless set; the local stack's and CI's environment files are the only places to set it, never staging or production: a hosted API or worker whose `KHADBOT_WCL_REDIRECT_URI` is not plain-HTTP localhost refuses to start with replay set, because replay answers report access checks from the recordings.

`main.py api` and `main.py worker` log a warning at start-up that replay is on, with the directory, the simulated limit and the live report, and stop at once when the settings can't work (no such directory, a live report that isn't recorded there). Every other process that opens a WCL client logs the same line the first time it does.

| Variable | Meaning |
|---|---|
| `KHADBOT_WCL_REPLAY` | A directory laid out like the raw page cache: `<code>/report.json` and `<code>/<fight>/<spec>/<filter_hash>/page_NNN.json`, optionally `<code>/batch/...` streams. `tests/fixtures/encounter/wcl` (after `git lfs pull`) is one. Empty turns replay off (the default). |
| `KHADBOT_WCL_REPLAY_LIMIT` | Points per window of the simulated `rateLimitData` counter (default 3600, the current scheme's limit). Set it low (say 40) to see an ingest wait in `waiting_budget`. |
| `KHADBOT_WCL_REPLAY_RESET_S` | The counter's window in seconds (default 3600). `pointsResetIn` counts down to its end; the next window starts with nothing spent. |
| `KHADBOT_WCL_REPLAY_LIVE` | A recorded report that is "still being logged": its metadata shows only its recorded fights, one at a time, so a live watch sees a raid in progress. Empty for none. |
| `KHADBOT_WCL_REPLAY_LIVE_INTERVAL_S` | 0 (default): one more fight with every metadata request (every watch poll). Above 0: one fight at the first request and one more every so many seconds. |

**Ingest asks for the recorded fetch shape.** Under replay, `wiring.py` gives ingest jobs the filters the real recordings were fetched with, the same ones offline recorded ingest uses: `RECORDED_BUFF_IDS` for defensive Buffs, no offensive Buffs, and enemy Casts unfiltered. It also turns cross-fight batching off, so every spec is fetched per pull, as the recordings were made. The filters the registries and encounter files give now have other filter hashes (the defensive registry has grown, the offensive one was never recorded), and no recording holds those pages; the fetch and ingest code is the same either way. So a replayed ingest shows what production ingest does, but not with production's filters, and its point spend is per pull rather than batched. `SYNTH01` was recorded with its own synthetic filters and encounter file, so a stock worker can't replay it; the tests replay it with those filters.

**What is refused.** A page, filter or fight that wasn't recorded, a multi-fight request with no recorded `batch/` stream, any other query and any other URL raise `WclReplayRefusedError`, a WCL API error whose message names the report, fight, spec and filter hash (so a registry change that outgrows the recordings is obvious). A pull whose pages are refused fails with that message and the rest of the ingest goes on. A report code with no recording answers as WCL does for a missing report, with a GraphQL error. A recording still in Git LFS pointer form is refused with the `git lfs pull` hint. The full request-to-page mapping is in `docs/wcl/replay.md`, "WCL replay".

**Points.** Every answered request costs 1 point, the measured cost of a one-page request; a `rateLimitData` read shows the count before its own point, and refusals cost nothing. With no point free the answer is HTTP 429 with no `Retry-After` (WCL isn't known to send one), which ingest retries and the budget sees through its reads. The counter lives in each process: the worker's is the one the budget syncs from, and the API's (access checks) counts apart. The budget assumes an hour between resets after the first one, so with a short `KHADBOT_WCL_REPLAY_RESET_S` it learns of the next reset at its next read (after each job and when a worker starts).

**Credentials.** Replay never sends the WCL client credentials anywhere (the app token is a local placeholder), but `WarcraftLogsConfig` still requires `WARCRAFTLOGS_CLIENT_ID` and `WARCRAFTLOGS_CLIENT_SECRET` to be set, so an environment without `.env` (CI) sets placeholder values.

Try it on the local stack: set `KHADBOT_WCL_REPLAY=tests/fixtures/encounter/wcl` (an absolute path when the process runs elsewhere) for the API and the worker, sign in, and ingest `QkVa6GHRK8yfrTbw` with fight 14 (the report lists every fight, but only the recorded ones have pages, so name them). For live watch, add `KHADBOT_WCL_REPLAY_LIVE=H7z1fqDXjRTFnbtg` and a short `KHADBOT_WATCH_POLL_INTERVAL_S`, then watch that report: its four recorded fights arrive one per poll.

### Fake model provider

A development switch (`docs/decisions.md`, D97): `LLM_PROVIDER=fake`, or `LLM_PROVIDER_ANALYST=fake` for one role, answers every model call from a script in `src/khadbot/llm_fake.py`, with no network and no key. Reviews run end to end on the local stack for nothing. Off unless set; the local stack's and CI's environment files are the only places to set it. `main.py api` and `main.py worker` log a warning at start-up naming the roles it answers for.

| Setting | Meaning |
|---|---|
| `FAKE_LLM_SCRIPT` | `default` (the default) or `failing`. `failing` adds one finding whose number no record holds, so verify rejects it and the review shows a rejected finding. A role without a script of that name plays its `default`. |
| `FAKE_LLM_DELAY_SECONDS` | Seconds each reply waits (default 0), so progress is visible. |

**Analyst.** The script calls `list_pulls`, then `query_deaths` when the pull has deaths (or `compare_pulls` when synthesising), then `submit_report`. Every finding is built from the records those tools returned: refs resolve and numbers match, so verify accepts them.

**Usage.** Every reply reports 1000 input and 200 output tokens (no cache tokens) under the model name `fake`. The price table lists `fake` at zero, so ledger rows carry a cost of 0, and findings are cached under the model key `fake`, never a real model's.

**Other roles.** A role with no script (the coach, until the conversation agent has one) answers with a fixed placeholder line. A script is added with `llm_fake.register_script(role, name, script)`.

### Seed (`scripts/data/seed_stack.py`)

The API's variables. It refuses to run unless `KHADBOT_MODE=hosted` and `KHADBOT_STORAGE=s3`, so it can never write a local data directory. It needs no WCL credentials.

### Containers in the local stack

| Variable | Meaning |
|---|---|
| `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` | The Postgres container's initial user and database. `DATABASE_URL` must match. |
| `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` | Also the one S3 identity the SeaweedFS container creates at start, so KhadBot and the store agree. |
