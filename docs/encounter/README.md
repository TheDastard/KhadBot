# Encounter mode

How encounter mode turns a WarcraftLogs report into records, a digest and verified findings. The models are in
`src/khadbot/encounter/models.py`; why they are shaped this way is in `docs/decisions.md`.

| File | Covers |
|---|---|
| [pipeline.md](pipeline.md) | Each stage (fetch, normalize, event store, annotate, analysis, digest, analyst, read models): what it stores and reads, what invalidates it, retention, cost and size |
| [analysis-rules.md](analysis-rules.md) | Encounter files, hits and windows, phases and anomalies, aggro, positions, fixture goldens |
| [failure-rules.md](failure-rules.md) | Failure rules and blame: when a mechanic's row is a failure and who is charged |
| [analyst.md](analyst.md) | What refs resolve to, query tool shapes, verify rules, step accounting, the eval harness, findings caching and synthesis |
| [recorded-nights.md](recorded-nights.md) | What the recorded Ula'tek nights showed: report facts, log investigations, friendly damage |

## Running the stages

| Stage | REPL command | Entry point |
|---|---|---|
| Fetch, normalize, store, records | `/ingest <code> [--fights 3,4] [--offline \| --refresh-meta]` | `uv run python main.py ingest <code> [--fights 3,4] [--offline] [--refresh-meta]` |
| List stored pulls | `/pulls [<code>]` | none |
| Annotate, analyse, digest | `/digest [<code>] <fight> [--json] [--unregistered]` | `uv run python main.py digest <code> <fight> [--json] [--unregistered]` |
| Analyst review and findings | `/review [--report <code>] [--fights 3,4] [--depth quick\|standard\|deep] [--estimate] [question]` | none |

- `--offline` reads only the raw page cache and fails on a missing page instead of calling WCL.
- `--refresh-meta` refetches `report.json` only and re-normalizes stored pulls from their cached pages (the specs of
  their fetch manifest), so a field added to the report query reaches pulls ingested before it. It cannot be combined
  with `--offline`.
- `--unregistered` prints the top unregistered and unclassified friendly-damage abilities (up to 20 each) instead of
  the digest. It is an authoring aid for the encounter file and the friendly-damage registry.
- `--estimate` prints the review's token estimate without calling a model.
- `main.py` (the REPL, `ingest`, `digest`) logs to stderr as redacted text at WARNING, so stdout carries only the
  command's output (`digest --json` stays parseable). `KHADBOT_LOG_LEVEL` and `KHADBOT_LOG_FORMAT` override that
  ([operations](../hosting/operations.md)).

Annotation runs inside `/digest` and `/review`; it has no command of its own.

### Job functions

`/ingest`, `/digest`, `/review` and the `ingest` and `digest` entry points run through the job functions in
`src/khadbot/jobs/`, the same functions the hosted queue runs. A job takes a spec, a `JobContext` and a `Progress`:

| Job | Spec | Does | Progress `(stage, pull)` |
|---|---|---|---|
| `run_ingest_report` | `IngestReport(code, fights, auth, offline, refresh_meta)` | Stage 1 only: report metadata, raw pages and a fetch manifest per completed pull; never the event store. Returns the `IngestResult`: every pull in `pulls` (pages or manifest written now) and `skipped` (already fetched with the same specs) is ready to process | `("ingest", None)`, `fetched <spec>` per pull (several pulls at once, interleaved), `("ingest", None)` |
| `run_process_pull` | `ProcessPull(pull_key)` | Stages 2 and 3 from the manifest's pages (or the stored events kept when the raw pages are gone), then stages 4 and 5: annotate, build and store the records; then project the read models when a projector is wired (hosted) | `("normalizing", pull)` twice, `("records", pull)` twice, then `("project", pull)` twice with a projector |
| `run_review_pull` | `ReviewPull(pull_key, depth, model)` | Stage 7 for one pull, without the synthesis: cached findings, or a fresh analysis that is cached | `("review", pull)` twice |

A job is idempotent: a second ingest finds every page and manifest cached, makes no WCL request and writes nothing;
a second process job finds stage 3's hashes, the annotation and the records stored and writes nothing; a second
review finds the findings. The CLI's `/ingest` and `main.py ingest` run the ingest job, then the process job for each
pull it left ready, in order, and print which pulls had their events written, which were unchanged and which failed
in either job. A spec holds `AppAuth` or `UserAuth(user_id)`, never a token; the context's `TokenResolver`
resolves it to ingest's `token_provider` at run time, and local mode resolves `AppAuth` only. `/digest` runs
`ProcessPull` without a records store, and `/review` runs it for each pull before the analyst reads them.

In hosted mode the queue (`jobs/queue.py`, run by `main.py worker`) runs the same functions. It adds no stage and
changes what none of them stores, reads or invalidates; it changes when they run:

- An ingest job queues a `ProcessPull` for every pull it fetched now *and* every pull it found already fetched.
  Processing an already processed pull finds the events, annotation and records stored and writes nothing, and
  queueing the already fetched ones as well means a pull fetched by an ingest that was cancelled before it queued its
  process jobs is processed by the next run.
- The ingest worker only does I/O: normalizing (about a second of CPU per pull) runs in the process job, in a worker
  thread. Process jobs for different pulls of one report run in parallel; their writes to the report's `pulls.json`
  take a per-report lock (a Postgres advisory lock in hosted mode).
- A running ingest can be cancelled. Every pull being fetched stops right after the page it is writing to the raw
  page cache and no further pull starts, so the pages written so far stay (a pull's manifest is written only once
  all its specs are complete) and the next ingest resumes from the cache like any interrupted one.
