# Encounter data pipeline

How a WarcraftLogs report becomes the digest, records and findings encounter mode works from: what each stage does,
what it stores, what it reads from `config/`, and what invalidates it. Runtime data lives under `KHADBOT_DATA_DIR`
(default `.khadbot_data/`). The models are in `src/khadbot/encounter/models.py`; the reasons behind them are in
`docs/decisions.md`.

```
WCL ──fetch──> raw/ ──normalize──> events/ ──annotate──> annotated/ ──analysis──> PullRecords ──caps──> PullDigest ──render──> text
                 ▲                                ▲                                ▲                                   │
   encounter-file ability IDs,      encounter file (one         cooldown files, friendly-damage                analyst ──verify──> findings
   cooldown-file IDs                difficulty's view)          registry, encounter file
   (fetch filters)
```

Fetch, normalize and annotate write to disk. `PullRecords` and the digest are rebuilt on request (`EncounterData`
keeps recent ones in memory), which is cheap while the annotation cache hits. Findings are the only persisted
model output.

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

## 1. Fetch: raw page cache

`encounter/ingest/fetch.py` pages through WCL's `events` query once per fetch spec (a data type, a hostility and an
optional filter) and writes every page to disk as it arrives. An interrupted ingest resumes from the last page; a
finished one never calls WCL again.

```
raw/<report>/report.json                                   # reportData.report: fights, masterData actors and abilities, phases
raw/<report>/<fight>/<spec>/<filter_hash>/page_000.json    # WCL JSON as returned
raw/<report>/<fight>/<spec>/<filter_hash>/_complete        # written after the last page
```

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

Other readers of analysis, such as a dashboard or desktop app, read `PullRecords` through `EncounterData` for now,
the same records the analyst cites, rather than the digest or the cache files. How to serve many views at scale is
still to be investigated (records are rebuilt per process and not persisted).

## What invalidates what

Every cache is keyed by content hashes, so a change takes effect at the first stage whose inputs it touches and flows
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
`PullDigest.produced_from`, so they key the in-process records and the findings. The annotation sidecar keeps a
narrower hash of only the pull fields annotation reads. The one gap left is a code change whose author forgot to
bump a version constant.

## Cost and size

WCL bills by points, with a budget of 3,600 points per hour for the app. With the default filters a pull costs about
14 points, so a full raid night is a small share of the hourly budget. Raw pages dominate disk use: a night is
hundreds of MB of JSON and tens of MB of parquet. Measured costs and their method are in `docs/wcl-api.md`.

`mise run clean` never touches `KHADBOT_DATA_DIR`. Deleting `raw/` means the next ingest refetches from WCL; deleting
`events/` or `annotated/` only costs local recomputation (an offline re-ingest rebuilds `events/` from `raw/`).
