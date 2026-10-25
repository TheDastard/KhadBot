# Encounter data pipeline

How a WarcraftLogs report becomes the digest, records and findings encounter mode works from: what each stage does,
what it stores, what it reads from `config/`, and what invalidates it. Runtime data lives under `KHADBOT_DATA_DIR`
(default `.khadbot_data/`). The models are in `src/khadbot/encounter/models.py`; the reasons behind them are in
`docs/decisions.md`.

```
WCL ──fetch──> raw/ ──normalize──> events/ ──annotate──> annotated/ ──analysis──> PullRecords ──caps──> PullDigest ──render──> text
                 ▲                                ▲                                ▲  │                               │
   encounter-file ability IDs,      encounter file (one         cooldown files, friendly-damage          records/ (persisted)    analyst ──verify──> findings
   cooldown-file IDs                difficulty's view)          registry, encounter file
   (fetch filters)
```

Fetch, normalize, annotate and analysis write to disk. `EncounterData` serves `PullRecords` from its in-process cache,
then from the records store, and builds and stores them only when neither has them (stage 5). The digest is built
with the records. Findings are the only persisted model output.

The raw page cache, the event store, the annotation cache and the records store read and write whole objects through
a `BlobStore` (`src/khadbot/storage/`), under keys equal to the paths below relative to `KHADBOT_DATA_DIR` (`raw/…`,
`events/…`, `annotated/…`, `records/…`). `storage.default_blob_store()` picks the store. By default it is
`LocalBlobStore` over the data directory, which reads and writes exactly the files the stages wrote before the seam
existed. With `KHADBOT_STORAGE=s3` it is an `S3BlobStore` over a bucket (`KHADBOT_S3_*`), and the objects under `raw/`
are stored zstd-compressed with a `.zst` suffix (`raw/<report>/report.json.zst`, `.../page_000.json.zst`,
`.../_complete.zst`); the stages still use the plain keys, and the other prefixes are stored as they are. Every put is
atomic (a reader sees the old object or the new one), and local listings skip the `*.tmp` files an interrupted write
leaves. The findings and checkpoint SQLite databases are not blobs and stay files under the data directory.

## Running the stages

| Stage | REPL command | Entry point |
|---|---|---|
| Fetch, normalize, store | `/ingest <code> [--fights 3,4] [--offline \| --refresh-meta]` | `uv run python main.py ingest <code> [--fights 3,4] [--offline] [--refresh-meta]` |
| List stored pulls | `/pulls [<code>]` | none |
| Annotate, analyse, digest | `/digest [<code>] <fight> [--json] [--unregistered]` | `uv run python main.py digest <code> <fight> [--json] [--unregistered]` |
| Analyst review and findings | `/review [--report <code>] [--fights 3,4] [--depth quick\|standard\|deep] [--estimate] [question]` | none |

- `--offline` reads only the raw page cache and fails on a missing page instead of calling WCL.
- `--refresh-meta` refetches `report.json` only and re-normalizes stored pulls from their cached pages, so a field
  added to the report query reaches pulls ingested before it. It cannot be combined with `--offline`.
- `--unregistered` prints the top unregistered and unclassified friendly-damage abilities (up to 20 each) instead of
  the digest. It is an authoring aid for the encounter file and the friendly-damage registry.
- `--estimate` prints the review's token estimate without calling a model.

Annotation runs inside `/digest` and `/review`; it has no command of its own.

### Job functions

`/ingest`, `/digest`, `/review` and the `ingest` and `digest` entry points run through the job functions in
`src/khadbot/jobs/`, the same functions the hosted queue runs. A job takes a spec, a `JobContext` and a `Progress`:

| Job | Spec | Does | Progress `(stage, pull)` |
|---|---|---|---|
| `run_ingest_report` | `IngestReport(code, fights, auth, offline, refresh_meta)` | Stages 1 to 3 for the report; returns the `IngestResult` (its `pulls` are the ones written) | `("ingest", None)`, ingest's own `fetched <spec>` and `normalizing` per pull, `("ingest", None)` |
| `run_process_pull` | `ProcessPull(pull_key)` | Stages 4 and 5: annotate, build and store the records; then project the read models when a projector is wired (hosted) | `("records", pull)` twice, then `("project", pull)` twice with a projector |
| `run_review_pull` | `ReviewPull(pull_key, depth, model)` | Stage 7 for one pull, without the synthesis: cached findings, or a fresh analysis that is cached | `("review", pull)` twice |

A job is idempotent: a second run finds stage 3's hashes, the annotation, the records or the findings already stored
and writes nothing. A spec holds `AppAuth` or `UserAuth(user_id)`, never a token; the context's `TokenResolver`
resolves it to ingest's `token_provider` at run time, and local mode resolves `AppAuth` only. `/digest` runs
`ProcessPull` without a records store, and `/review` runs it for each pull before the analyst reads them.

## 1. Fetch: raw page cache

`encounter/ingest/fetch.py` pages through WCL's `events` query once per fetch spec (a data type, a hostility and an
optional filter) and writes every page to disk as it arrives. An interrupted ingest resumes from the last page; a
finished one never calls WCL again.

```
raw/<report>/report.json                                   # reportData.report: fights, masterData actors and abilities, phases
raw/<report>/<fight>/<spec>/<filter_hash>/page_000.json    # WCL JSON as returned, or one page split from a batch
raw/<report>/<fight>/<spec>/<filter_hash>/_complete        # written after the last page
raw/<report>/batch/<spec>/<filter_hash>/<fights_hash>/page_000.json   # one stream for several fights (transient)
raw/<report>/batch/<spec>/<filter_hash>/<fights_hash>/_complete       # written after the last page
raw/<report>/batch/<spec>/<filter_hash>/<fights_hash>/stalled         # timestamp where a stream stopped advancing
```

Batch entries are transient: they exist only until their stream has been split by each row's `fight` into the
per-fight entries, which stay the only thing normalize reads. `fetch_batches` fetches the eight small specs
(`BATCHED_SPEC_KEYS`) once per spec, filter and chunk of up to `wcl_batch_max_fights` pulls; pulls already partly
fetched, single pulls, offline and metadata-refresh runs go per pull, and a failed batch falls back to per-pull requests.

Specs per pull (`DEFAULT_SPECS` plus `specs_for`):

| Spec | Filter |
|---|---|
| Deaths, DamageTaken, Casts (friendly), Debuffs, Interrupts, Dispels, Healing | none |
| Casts, enemies | the encounter file's ability IDs, every difficulty |
| Buffs | the defensive cooldown file's cast and aura IDs |
| Buffs `:offensive` | the offensive cooldown file's cast and aura IDs; left out when the file is empty |
| Threat (friendly side) | none: NPC melee swings at players, taunts, NPC deaths |

The authored files decide part of what is downloaded: a buff no cooldown file names is never fetched. `filter_hash`
(the first 12 hex of SHA-256 of the filter) is part of the path, so a changed filter refetches only its own spec. The
utility cooldown file and the friendly-damage registry add no request; utility casts come from the unfiltered
friendly Casts.

Not fetched: damage done to enemies, enemy-side Threat and enemy Debuffs. Data that is not fetched is unavailable to
every later stage.

Requests are retried with backoff on WCL rate limits; `EncounterConfig` sets the page size, concurrency and retry
count.

`ingest_report` and `fetch_pull` take an optional `token_provider`, an async callable returning a signed-in user's WCL
access token. With it every request (report metadata, event pages and the points reads) goes to `/api/v2/user` with
that token, and `points_spent` counts the user's budget; without it they use the app token. The provider is called
for every request so it can refresh. On an HTTP 401 it is called once more and the request retried once; a second
401 raises `UserTokenRejectedError`. When ingest fetches `report.json` it adds one key, `khadbotFetchedWith`, set to
`"user"` or `"app"`: which kind of token fetched it, never which user. `"user"` means the report may be private. A
`report.json` without the key (every directory written before it) reads as unrecorded, and a cached `report.json`
is never rewritten by a later ingest, so the key describes the fetch that wrote the file.

## 2. Normalize

`encounter/ingest/normalize.py` turns the raw JSON into typed tables. It flattens and cleans; it does not interpret.

- **Report metadata:** `Pull` (fight ID, encounter, difficulty, kill, start, end and duration, WCL phase transitions,
  fight progress and boss health, friendly player IDs) and `Actor` (player, pet, boss or NPC; pet owner; game ID).
  `abilities.json` is WCL's `{ability_id: name}` lookup.
- **Events:** one polars frame per pull with `store.EVENT_SCHEMA` (20 columns): `seq`, fight-relative `t_ms`,
  `data_type`, `type`, source and target IDs and instances, `ability_id`, `extra_ability_id`, `amount`, `absorbed`,
  `overkill`, `overheal`, `unmitigated`, `hit_type`, `stack`, `hp_pct` and the two friendly flags.
- **Fixes:** death rows get the killing ability and killer copied into `ability_id` and `source_id`; Threat rows that
  repeat Casts or Deaths (player casts, player deaths) are dropped; rows are stably sorted by time, then data-type
  order, and `seq` is assigned after the sort.
- **Positions:** in the same pass, every row carrying `x`/`y` gives a sample (`t_ms`, actor, instance, x, y, map) for
  the unit `resourceActor` names, deduplicated. Rows dropped from the events frame still count.

## 3. Event store

`encounter/store.py`, behind the `EventStore` protocol (polars and parquet today):

```
events/<report>/pulls.json                 # [{pull, events_hash, positions_hash, truncated: [spec]}]
events/<report>/actors.parquet
events/<report>/abilities.json
events/<report>/<fight>.parquet            # the normalized event frame
events/<report>/positions/<fight>.parquet  # position samples
```

`events_hash` and `positions_hash` are content hashes. A re-ingest whose hashes and pull metadata match is skipped
without a write.

## 4. Annotate: annotation cache

`encounter/annotate/` tags each event with what the encounter file says it means. It reads only the encounter file
(the view for the pull's difficulty), the position table and the aggro reconstructed from Threat rows; the cooldown
files and the friendly-damage registry are not used here.

- **Phases:** ordered triggers (`pull`, `boss_cast`, `boss_aura_applied`, `boss_hp_below`, `time`, `wcl_phase`)
  become spans; detection stops at the first trigger that never fires.
- **Mechanics and damage windows:** `ability_id` maps to `mechanic_id` and `window_id`; consecutive hits within
  `cluster_window_ms` form a cluster or a window occurrence.
- **Failures and blame:** each mechanic's failure rule marks failure rows and the player charged (`blamed_id`).
  `per_cluster` blame also records where that player was: in or beyond reach, dead, or without a position.
- **Wipe cutoff:** when cumulative player deaths reach `ceil(cutoff_fraction × raid size)`; kills have none.

The cached frame is the whole event frame plus ten columns (`phase_id`, `mechanic_id`, `cluster_id`, `is_failure`,
`blamed_id`, `window_id`, `window_occurrence`, `blame_state`, `blame_dist_yd`, `blame_pos_age_ms`), with a JSON
sidecar for the phase spans, cutoff, unmatched phase, input hashes, `ANNOTATION_VERSION` and a hash of the pull
metadata annotation reads (start, end, duration, kill, phase transitions, friendly player IDs):

```
annotated/<report>/<fight>__<encfile_hash>.parquet
annotated/<report>/<fight>__<encfile_hash>.json
```

## 5. Analysis: `PullRecords`

`digest.build_pull_records` loads the defensive, offensive and utility cooldown files and the friendly-damage
registry, then runs `encounter/analysis/` over the annotated frame. The records are uncapped, and every evidence ref
(`ev:<report>:<fight>:<kind>:...`) resolves against them.

The records are stored by `encounter/records_store.py`, so a process that did not build a pull's records reads them:

```
records/<report>/<fight>__<hash>.json
```

`<hash>` is the first 16 hex of SHA-256 over the digest's `produced_from` (`digest.records_inputs`): the events,
positions, encounter-file, three cooldown-file and friendly-damage hashes, `ANNOTATION_VERSION`, `RECORDS_VERSION`,
the cutoff fraction and `pull_hash`. A blob is never updated; changed inputs give a new key and the old blob stays
(nothing deletes them yet). The JSON holds the deaths, mechanics, cooldown uses, window coverage and the capped
digest (`StoredRecords`). The annotated frame, the pull, the actors and abilities and the encounter file are not in
it: a load reads the frame through the annotation cache (annotating again if it is missing), the rest from the event
store and `config/`.

`EncounterData.records` reads, in order, its in-process cache (32 pulls), the records store, and then builds the
records and writes them to the store. A blob that fails to parse or whose `produced_from` differs from the inputs is
a miss, logged, and is overwritten by the rebuild. A failed write is logged and the read still succeeds. A store
built without a records store (tests, `build_digest`) neither reads nor writes `records/`.

| Record | From | Holds |
|---|---|---|
| `DeathRecord` per player death (`death:<n>`) | `deaths` | killing blow, overkill, time to die, damage taken, healing and self-damage in the last 10 s, top 5 hits, mechanics involved, defensives active |
| `MechanicStat` per mechanic (`mech:<id>`) | `mechanics` | hits, clusters, failures, failures by player, damage, deaths whose window it was in, blame states; the ref also resolves to per-cluster rows |
| `CooldownUse` per cast (`cd:<spell>:<n>`) | `cooldowns` | category (defensive, offensive, utility), caster, time, phase |
| `WindowCoverage` per window occurrence (`win:<id>:<n>`) | `cooldowns` | defensive raid cooldowns active at the first hit, covered or not, late ones; defensive file only |
| `FriendlyDamage` | `friendly_damage` | damage from the raid's own abilities by class, self-inflicted damage per player, unclassified abilities |
| `AggroSummary` per boss NPC | `aggro` | melee swings by target (top 3), taunts landed |
| Unregistered damage | `digest` | damage from abilities the encounter file doesn't name, with each one's share |

A `pull` ref resolves to the pull summary: outcome, progress, death counts, coverage, anomalies, aggro, friendly and
unregistered damage. An `event:<seq>` ref resolves to one annotated event.

## 6. Digest

`PullDigest` is built from `PullRecords` by capping and ordering. It is not stored. The caps are constants in
`digest.py`.

| Section | Contents |
|---|---|
| Header | outcome, duration, phase reached, fight progress, boss health, cutoff, coverage |
| DEATHS | capped, before-cutoff first |
| MECHANICS | mechanics with hits or failures, capped, by failures then damage |
| DEFENSIVE COOLDOWNS | capped, time order |
| DAMAGE WINDOWS | every occurrence |
| OFFENSIVE COOLDOWNS, UTILITY COOLDOWNS | capped separately; raid-wide casts rank first, then one cast per spell at a time; shown in time order |
| AGGRO, SELF DAMAGE | one line per boss NPC; friendly damage by class and per player |
| UNREGISTERED DAMAGE, UNCLASSIFIED FRIENDLY DAMAGE | capped, by damage |
| ANOMALIES | codes, below |

`render_digest` writes one line per record, each starting with its evidence ref, with times as `12.4s` and numbers
with thousands separators so verify can match them. The rendered digest has a token target, estimated as
`len(text) // 4`; `tests/unit/encounter/test_real_fixtures.py` enforces its ceiling over the recorded pulls.

**Coverage** is `none` without an encounter file for the pull's encounter and difficulty, `full` when unregistered
abilities account for under 10% of friendly damage taken, and `partial` otherwise.

### Anomaly codes

| Code | Meaning |
|---|---|
| `truncated:<spec>` | Pagination for that spec stopped without advancing; its data may be incomplete. |
| `missing_data_type:<type>` | DamageTaken, Casts or Healing has no rows, which means the fetch is missing, not that nothing happened. |
| `no_hp_data` | No event carries health, so time to die and health-based phase triggers are unavailable. |
| `phase_trigger_unmatched:<phase>` | WCL's phase transitions say the pull reached a phase the encounter file's triggers did not detect. |
| `cooldown_unverified:<spell>` | A defensive or offensive cooldown was cast whose entry is unverified or missing. Utility casts are not checked. |
| `unregistered_damage:<pct>` | With an encounter file, unregistered abilities took at least 10% of friendly damage taken. |
| `friendly_damage_unverified:<id>` | An unverified friendly-damage registry ability hit a player. |
| `unclassified_friendly_damage:<pct>` | Damage from friendly players or pets that the registry does not class reached 10% of friendly damage taken. |

## 7. Analyst and findings

The analyst (`encounter/analyst/graph.py`) answers a question about one or more pulls. It starts from the rendered
digest (one pull) or the pull list (several), calls the read-only query tools within a step budget set by depth, and
ends with `submit_report`. Verify (`analyst/verify.py`) rejects a finding whose refs don't resolve to a pull in the
request, or whose numbers don't appear in the resolved records; the model gets one retry with the reasons.

`/review` (`analyst/review_graph.py`) runs the analyst once per pull with a fixed review question, in parallel, then
synthesizes an answer to the user's question from every pull's findings. Synthesis runs on every review; per-pull
findings are cached in SQLite at `khadbot.sqlite` under `KHADBOT_DATA_DIR`. Partial reports and reports without a
structured submission are used but not cached. A flagged finding is left out of synthesis and its pull is
re-analysed on the next review.

The findings `cache_key` (`findings.py`) hashes the pull key, the events and positions hashes, the encounter-file
hash, the three cooldown-file hashes, the friendly-damage hash, `prompt_version`, `GRAPH_VERSION`, the model ID and
the depth. `prompt_version` covers the prompt templates, each tool's name, docstring and argument schema, and
`VERIFY_RULES_VERSION`, so a tool docstring edit is a prompt change.

### What the analyst can reach

The query tools (`analyst/query_tools.py`) read `PullRecords` and the annotated frame through `EncounterData`. None
reads the raw cache, the event store files, the position table or WCL. Results are capped by rows and characters;
`get_digest` is exempt because the digest's own caps bound it.

| Tool | Reads | Reach |
|---|---|---|
| `list_pulls`, `compare_pulls` | pull summaries, mechanic stats | one row per pull for a fixed set of metrics |
| `get_digest` | the rendered digest | one pull |
| `query_deaths` | every `DeathRecord` | filters by player and phase; after-cutoff deaths on request |
| `query_mechanic` | one `MechanicStat` plus its cluster rows | any mechanic in the encounter file |
| `cooldown_timeline` | every `CooldownUse` and `WindowCoverage` | filters by phase |
| `query_events` | the annotated frame | a window of at most 30 s, filtered by player, ability and event type |

## 8. Other consumers

Other readers of analysis, such as a dashboard or desktop app, read `PullRecords` through `EncounterData`, the same
records the analyst cites, rather than the digest or the cache files. Records persist (stage 5), so a reader process
does not rebuild what another one built. Views that aggregate across pulls (the report detail, the night summary,
progression) can instead read per-pull tables in Postgres projected from the records (stage 9); in local mode they
load every pull's records and annotated frame, as before.

## 9. Read models (hosted mode)

`db/read_models.py` projects one pull's records into small Postgres tables (`db/migrations/0002_read_models.sql`)
that hold what the night, report and progression views filter, sort or aggregate on. Everything else stays in the
records blob, which each `pulls` row names (`records_key`). Events stay in parquet.

| Table | One row per | Holds |
|---|---|---|
| `reports` | report | title, start time, the token kind that fetched it (`khadbotFetchedWith`), fetch time when the caller knows it; guild and visibility columns are empty until the report query fetches them |
| `pulls` | pull | the `Pull` fields the report detail and night summary show, the digest's phase reached (and its encounter-file name), coverage, cutoff, death counts, anomalies, `events_hash` and `encfile_hash` from `produced_from`, the records hash and key, `RECORDS_VERSION` and `READ_MODEL_VERSION` |
| `pull_deaths` | player death (`death:<n>`) | time, phase, killing ability, overkill, time to die, after cutoff |
| `pull_mechanics` | mechanic in the encounter file | its encounter-file name, clusters, hits, failures, damage taken, deaths in window |
| `pull_cooldowns` | cooldown cast (`cd:` ref) | spell, category, caster, time, phase |
| `pull_windows` | damage-window occurrence | its encounter-file name, time, covered, defensive cooldowns active, how many came late |

`project(pull, records)` is pure. `project_pull(pool, records, report_meta)` is the writer: in one transaction it
upserts the `reports` row, locks it (so one report's pulls are written one at a time), and, unless the pull's row
already has the same records hash and `READ_MODEL_VERSION`, deletes the pull's rows in all five tables and inserts
them again. Projecting the same records twice writes nothing, and a re-ingest, a changed authored file or a
`RECORDS_VERSION` bump (all of which change the records hash) replaces the pull's rows. A change to the projection
itself bumps `READ_MODEL_VERSION` (`read_models.py`), which re-projects every pull the next time it is projected.
In hosted mode the `ProcessPull` job projects each pull after it stores the records
(`db.PostgresReadModelProjector`); the CLI in local mode never does.

`api/night.py` builds the report detail and the night summary from per-pull inputs that a `ReadSource` supplies:
`RecordsSource` reads records through `EncounterData` and findings from the findings store (local mode, the
default), and `SqlSource` reads `db/queries.py` (the pull rows, each pull's first death and its hit or failed
mechanics, and the unflagged findings' hashes). Aggregation happens in Python on both paths, so they give the same
result when the tables were projected from the records the other path would load. A findings set is stale on the
SQL path when its events or encounter-file hash differs from the pull row's, which are the hashes the records were
built from. A pull not yet projected is missing from the SQL path's report. `create_app(read_source=...)` picks the
source; without one the API reads records, as before.

`queries.progression(conn, encounter_id, difficulty)` returns, per night (one report) in start order, the pulls,
kills and best boss health (0 with a kill), and each failed mechanic's failures and pulls failed. It reads
`pulls` through `pulls_encounter (encounter_id, difficulty, report_code)` and the child tables through their
primary keys, which start with `(report_code, fight_id)`.

## What invalidates what

Every cache is keyed by content hashes (`records/` by the `produced_from` ones), so a change takes effect at the first stage whose inputs it touches and flows
down from there. Changes to fetch filters take effect only when `ingest` runs again.

| Change | Re-ingest | Fetch | Annotation | Records and digest | Findings |
|---|---|---|---|---|---|
| Encounter file: ability IDs | yes | that encounter's enemy-Casts spec | rebuilt | rebuilt | miss |
| Encounter file: anything else | no | none | rebuilt for the edited difficulty only | rebuilt | miss |
| Defensive or offensive file: cast or aura IDs | yes | that file's Buffs spec | rebuilt (new `events_hash`) | rebuilt | miss |
| Defensive or offensive file: anything else | no | none | kept | rebuilt | miss |
| Utility file or friendly-damage registry | no | none | kept | rebuilt | miss |
| A field added to the report query | `--refresh-meta` | `report.json` only | kept | rebuilt | miss (`pull_hash`) |
| `--refresh-meta` changing pull start, end, duration, kill, phase transitions or friendly players | `--refresh-meta` | `report.json` only | rebuilt | rebuilt | miss (`pull_hash`) |
| `ANNOTATION_VERSION` or `EncounterConfig.cutoff_fraction` | no | none | rebuilt | rebuilt | miss |
| Analysis or digest code (bump `RECORDS_VERSION`) | no | none | kept | rebuilt | miss once bumped |
| Prompt template, tool docstring or schema, verify rules | no | none | kept | kept | miss (`prompt_version`) |
| Analyst graph logic | no | none | kept | kept | miss once `GRAPH_VERSION` is bumped |
| Analyst model or depth | no | none | kept | kept | miss |

`ANNOTATION_VERSION`, `RECORDS_VERSION` (`digest.py`, hand-bumped when analysis or record-building code changes
its output), the cutoff fraction and `pull_hash` (a hash of the whole stored `Pull`) are in
`PullDigest.produced_from`, so they key the in-process records, the stored records and the findings; a "rebuilt"
records cell above means the stored blob misses too and a new one is written beside the old. The annotation sidecar keeps a
narrower hash of only the pull fields annotation reads. The one gap left is a code change whose author forgot to
bump a version constant.

The read-model rows follow the records: every change that rebuilds a pull's records gives it a new records hash, so
the next projection of that pull replaces its rows; a change only to the projection code needs a
`READ_MODEL_VERSION` bump. Findings are not projected; the SQL path reads the `findings` table directly.

## Cost and size

WCL bills by points, with a budget of 3,600 points per hour for the app. With the default filters a pull costs about
14 points, so a full raid night is a small share of the hourly budget. Raw pages dominate disk use: a night is
hundreds of MB of JSON and tens of MB of parquet. Measured costs and their method are in `docs/wcl-api.md`.

Stored records, measured on a copy of a real 21-pull night (a kill and wipes of one raid, real encounter files and
registries): 1.66 MB of `records/` against 23.2 MB of `annotated/` and 29.4 MB of `events/` for the same night, about
79 KB per pull (the seven recorded fixture pulls average 78 KB). Time to get one pull's records, on the dev machine
(Windows, local disk, best of three, a fresh `EncounterData` per pull, annotation cache warm):

| Path | Per pull | Whole 21-pull night |
|---|---|---|
| Build (analysis over the annotated frame) | about 0.5 s | 10.5 s |
| Load from the records store | about 0.19 s | 3.9 s |

With the cooldown files and registry passed in rather than read from `config/`, the fixture pulls take about 240 ms
to build and 39 ms to load. The rest of a load was YAML parsing of the authored files, which every lookup used to
repeat (about 115 ms for the four registry files and tens of ms for the encounter file); parsing the blob itself
takes about 1.3 ms. Lookups now check each file with one `stat` and reparse it only after it changed (D58). With
the annotation cache cold, a load still re-annotates the frame, so it saves only the analysis step; the annotation
cache is part of what a second process needs to find.

`mise run clean` never touches `KHADBOT_DATA_DIR`. Deleting `raw/` means the next ingest refetches from WCL; deleting
`events/`, `annotated/` or `records/` only costs local recomputation (an offline re-ingest rebuilds `events/` from `raw/`).
