# KhadBot decisions log

Source plan: <https://claude.ai/artifact/Q45H17JuH5EXK8oz17CzQE> (Encounter Review Refactor v0.3).

## Decisions

| # | Decision | Choice | Why | Date |
|---|----------|--------|-----|------|
| D1 | Mode dispatch | Deterministic `Session.mode` (`coach` / `encounter`), switched by `/mode`; no LLM router | Predictable, testable, no routing errors | 2026-10-10 |
| D2 | Legacy encounter code | Leave untouched until M5, then delete | Not reachable by any agent today (not in `coach.yaml`), so patching buys nothing | 2026-10-10 |
| D3 | Engineering principles | Record the principle changes here; updating the external doc is H7 | Doc is outside the repo | 2026-10-10 |
| D4 | Models per role | `get_llm(provider, *, role=)`; `LLM_PROVIDER_<ROLE>` and `<ROLE>_MODEL` env overrides | Analyst and conversation have different cost/quality needs | 2026-10-10 |
| D5 | Prompt versioning | `prompt_version` = SHA-256[:12] of prompts, tools, schemas, verify rules; `GRAPH_VERSION` manual | Cache keys invalidate when behaviour changes | 2026-10-10 |
| D6 | Ingest execution | Inline async with progress callback; resumable via raw page cache; idempotent via `events_hash` | No job queue needed for a CLI | 2026-10-10 |
| D7 | Event store | polars + parquet, one file per pull, behind an `EventStore` protocol (S2 may swap to DuckDB) | Cheap, columnar, no server | 2026-10-10 |
| D8 | Runtime | Python only, target 3.13, `requires-python >=3.12` | Matches the venv | 2026-10-10 |
| D9 | Local persistence | Everything under `KHADBOT_DATA_DIR` (default `.khadbot_data/`); SQLite for findings/plans; `AsyncSqliteSaver` for checkpoints (M5) | One gitignored directory | 2026-10-10 |
| D10 | Package location | One sub-package, `src/khadbot/encounter/` (with `analysis/`) | Justified exception to "flat files preferred" | 2026-10-10 |
| D11 | Analyst output | Analyst ends by calling a `submit_report` tool whose schema is `AnalysisReport` | Works across Groq, Ollama, Anthropic and fakes | 2026-10-10 |

## Implementation notes (deviations from the plan text)

- **Raw cache directory for enemy data**: `<DataType>-Enemies` is used for `hostility="Enemies"` so friendly and
  enemy `Casts` do not collide when both have `filter=none` (plan 5.2 only names `<DataType>`).
- **`build_filter` inputs (M1.3)**: takes plain ID lists (`registry_spell_ids`, `encounter_ability_ids`) instead of
  `CooldownRegistry` / `EncounterFile`, because those models arrive in M2.1. M2 passes IDs derived from them.
  `ingest_report` takes the same two optional lists.
- **`EventStore` additions (M1.7)**: `write_pull` takes an optional `truncated` list and the protocol gains
  `truncated(pull_key)`, so truncation flags live in `pulls.json` next to `events_hash` (plan M1.8).
- **Fetch keys**: `fetch_pull` returns `{dir_name: truncated}` (not `data_type`) so friendly/enemy `Casts` are distinct.
  "Truncated" means pagination stopped because `nextPageTimestamp` did not advance.
- **CLI ingest filters (M2.1)**: `/ingest` and `main.py ingest` now pass `registry_spell_ids` from
  `config/registries/cooldowns.yaml` and `enemy_cast_ids` from every `config/encounters/*.yaml`
  (`EncounterFile.ability_ids()`: mechanics, damage windows, phase-trigger abilities). A report whose boss has no
  encounter file still fetches enemy `Casts` unfiltered.
- **Cooldown registry kinds (M2.1)**: the 17 ported entries carry a first-pass `kind` (raid / external / personal);
  H4 verifies kinds along with durations. `class` is exposed as `CooldownEntry.class_name`.
- **Unknown keys in authored files are errors** (`extra="forbid"`), so typos fail fast.
- **mypy**: clean on `main` since fdaed26, which also added `types-pyyaml` to dev deps; the encounter work adds no errors.
- **Package casing (M0.1)** was committed on its own, as the plan requires, before the rest of M0.

### M2 (encounter file, annotation, digest)

- **SYNTH01 generator fix**: the sim applied damage and heals in script order, so `hitPoints` and deaths did not
  follow the timeline. It now applies them chronologically; deaths match the fixture README (fight 1: 8, 23, 41,
  50 s; fight 2: 55 s). Normalized goldens were regenerated in the same commit.
- **Ability names**: `EventStore` gains `write_abilities` / `abilities(code)` (`events/<report>/abilities.json`,
  from `masterData.abilities`) so death records and unregistered damage can name abilities. Reports ingested before
  M2 have no names until re-ingested.
- **Signatures vs plan section 10**: `annotate(pull, events, enc, actors, *, cutoff_fraction)` (no registry; nothing
  in annotation uses it). `Annotated` carries the `pull`, so analysis functions take no separate `pull`.
  `death_records(a, actors, registry=None, abilities=None)` needs the registry for `active_defensives`.
  `detect_phases` keeps its signature; `unmatched_phase(spans, enc)` names the trigger that stopped detection.
  `build_digest` adds keyword-only `cache`, `cutoff_fraction` and `unregistered_cap`.
- **Window coverage counts `kind: raid` cooldowns only** (plan 6.3 said raid/external). A damage window is raid-wide
  damage; an external on one player does not cover it. Plan section 6.3 needs the same edit.
- **Hits and clusters**: a "hit" is a `damage` event on a friendly target. Clusters, `window_occurrence`,
  `MechanicStat.hits/damage_taken` and the death window all use hits. Damage figures use WCL `amount` (after
  absorbs and overkill). `not_interrupted` failures are the enemy cast rows, so they have no player in
  `failures_by_player`; `soak_count_lt` failures mark a cluster's first row.
- **`time_to_die_ms`** is None when the actor had no reading at >= 90% HP in that life (not only when HP data is
  missing). A second death's window and time-to-die are clipped to after the previous death (battle res).
- **Anomalies**: `phase_trigger_unmatched:<id>` is raised only when the pull should have reached that phase (a kill,
  or WCL `last_phase` greater than the phases detected); an early wipe never reaching P2 is not an anomaly.
  `missing_data_type:<T>` checks only DamageTaken, Casts and Healing, which every real pull has; zero Deaths,
  Interrupts or Dispels rows are legitimate. `unregistered_damage:<pct>` is raised only when an encounter file
  exists and the share is >= 10%.
- **Triggers needing data not fetched yet**: `boss_hp_below` needs damage on the boss (no DamageDone spec) and
  `boss_aura_applied` needs auras on the boss (Buffs are fetched for friendlies only). They match nothing on real
  data until S1 adds those specs; prefer `boss_cast`, `wcl_phase` or `time` in H3 until then.
- **CLI**: `/digest [<report-code>] <fight> [--json] [--unregistered]` and `/pulls [<report-code>]` default to the
  last report ingested in the session. `--unregistered` lists up to 20 abilities. Golden files can be rewritten
  with `pytest --update-goldens` (`UPDATE_GOLDEN=1` still works).
- **Offline ingest of SYNTH01 through the CLI** misses the cache, because the CLI derives Buffs / enemy-Casts filters
  from `config/` while the fixture was recorded with synthetic IDs. Tests pass the synthetic IDs explicitly.
- **M2.6 (legacy comparison) is blocked on H2**: it compares against real fixture pulls, which do not exist yet.
- **M2 exit criteria waiting on humans**: H3 encounter file (<10% unregistered on real reports), real-fixture digest
  goldens and the 1.5k-3k token check (H2), perf budgets (`-m perf`, written, skipped until H2), and M2.6.

### M3 (analyst loop)

- **Refs resolve against uncapped records**: `digest.build_pull_records` returns every death, mechanic, cooldown and
  window (`PullRecords`) plus the capped digest; `build_digest` wraps it. A ref beyond a digest cap (for example the
  11th death) still resolves, and query tools can return it.
- **`EncounterData`** (`evidence.py`) is the read side for analysis: the store plus the encounter-file root,
  registry, annotation cache and cutoff fraction. It caches `PullRecords` in-process (bounded LRU keyed by
  pull_key, `events_hash` and `encfile_hash`). `resolve(ref, data)` takes `EncounterData` or a bare `EventStore`
  (which then uses `config/` files), instead of the plan's `resolve(ref, store)`.
- **What refs resolve to** (contract detail not in plan 6.5): `pull` → a summary (outcome, duration, boss %, phase
  reached, cutoff, death counts, coverage, anomalies); `mech:<id>` → the `MechanicStat` plus `cluster_rows`
  (`MechanicCluster`: per-cluster first/last hit, hits, players, damage, failures); `event:<seq>` → the annotated
  event with actor and ability names instead of actor IDs, nulls dropped, `hp_pct` rounded to 0.1. Query tools
  return exactly these dicts, so verify checks claims against what the model saw. `plan:` refs parse but do not
  resolve until raid plans exist.
- **Claim numbers** also accept `2:14` (m:ss), `12.4 seconds`, `250ms`, `1.2k` and `3.4m`; digits inside words
  (`Healbot2`, `p2`, `3rd`) and inside refs are ignored. A number matches if a record value, scaled to the claim's
  unit and rounded to the claim's precision, equals it.
- **Query tool shapes** (plan 6.6 names tools and args only): rows are the resolved record for their ref, or a
  subset of its fields (`list_pulls` and `compare_pulls` rows are slim pull summaries). `query_mechanic` returns
  `{"mechanic": stat, "clusters": [...]}`; cluster rows carry the mechanic's ref. `cooldown_timeline` returns
  cooldown uses and damage-window rows in one list sorted by time; a window's phase is the phase active at its
  first hit. `compare_pulls(metric="mechanic_failures")` rows carry the `mech:` ref. A bare fight ID is accepted
  as `pull` when exactly one pull in scope has it.
- **`get_digest` is exempt from the 4,000-character cap**: the rendered digest targets 1.5k-3k tokens, so the
  digest's own caps bound it instead.
- **`submit_report` arguments** are `findings` (claim, refs (min 1), confidence) and `unknowns`. The finding
  `id`, `scope`, request ID and `produced_by` are filled in by code, not the model.
- **`prompt_version` hashes prompt templates, not rendered prompts.** The rendered analyst prompt embeds the
  pull's digest, which would make the version differ per pull; the findings cache key already carries
  `events_hash` and `encfile_hash`. It also hashes the role and each tool's `tool_call_schema` (the schema the
  model is sent, without the injected run config) rather than `args_schema`. `VERIFY_RULES_VERSION` lives in
  `versioning.py` next to `GRAPH_VERSION`.
- **Verify rule additions** (plan 6.7, still `VERIFY_RULES_VERSION = 1` as the first implementation): a ref
  must resolve to a pull inside the request, not just any ingested pull; and the number of refs cited (overall
  and per kind) counts as a supported value, so "3 players died" citing three death refs passes. Numbers come from
  every field of the cited records, including IDs and digits inside strings, so a small count can match by
  coincidence; definitions that are not recorded values (the 90% time-to-die threshold) are rejected.
- **Rejection reasons** are recorded as `<finding id> "<claim>": <reason>`.
- **Analyst graph wiring**: `build_analyst_graph()` takes no store. The data, model, model ID, prompt version
  and tools travel in `config["configurable"]["analyst"]` (`AnalystRun`), never in graph state, and the tools node
  adds the `AnalystScope` to the config it passes to tools. `run_analysis(req, *, store, callbacks, run_id)`
  accepts `EncounterData` or a bare store, calls `llm_factory.get_llm(role="analyst")` once per run, and traces the
  run as `encounter_analyst` with the request ID, versions, model ID, pulls and depth as metadata. `run_id` lets the
  eval harness attach feedback to the trace.
- **Analyst state** adds `forced` (budget hit), `pulls` (requested pulls that are ingested) and `unknowns` (from
  code) to the plan's fields. Requested pulls that are not ingested become `pull <key> is not ingested` unknowns;
  if none are ingested the model is never called.
- **Step accounting**: a `submit_report` round costs a step like a query round. The single retry (for rejected
  findings or an invalid `submit_report` call) happens only if steps remain after the submission; otherwise the
  rejections stand. A forced final turn that calls anything but `submit_report` ends with
  `unknowns=["no structured report"]` and `partial=True`. Other tool calls made alongside `submit_report` are
  answered "ignored" and not run.
- **Golden eval harness**: question schema, loading and the deterministic checks live in `tests/helpers/golden.py`
  (unit-tested on SYNTH01); `tests/eval/` runs them on a real analyst model. Eval tests carry an `eval` marker and,
  like `perf`, are skipped unless selected (`-m eval`; `make test-eval` passes it). Recorded reports the questions
  name are ingested offline into a session store. The pass rate per model and ref-resolution counts are printed
  at the end and written to `$KHADBOT_DATA_DIR/eval/golden-<timestamp>.json`. `KHADBOT_EVAL_MIN_PASS_RATE` gates
  the run once thresholds are set (unset: report only); `KHADBOT_GOLDEN_QUESTIONS` points at another question
  file. With `LANGSMITH_API_KEY` set, each question's result is attached to its traced run as `golden_pass`
  feedback (the "upload" the plan mentions). Validation is stricter than the plan's schema: `unanswerable`
  requires `must_be_unknown: true`, and answerable questions need `expected_refs_any`. An optional `depth` field
  selects the step budget.
- **M3 exit criteria waiting on humans**: golden questions (H5; `tests/golden/questions.yaml` is an empty list with
  the schema in comments), thresholds and the Groq gate decision (H6), and the query-tool p95 perf test (written,
  skipped until H2 fixtures exist). Unanswerable handling and every scripted scenario pass on SYNTH01.

## Human task status

| ID | Task | Status |
|----|------|--------|
| H1 | Choose boss + three public reports | open |
| H2 / S1 | Record real fixtures, run cost spike | open |
| H3 / S3 | Author the encounter file for the H1 boss | open |
| H4 | Fill cooldown durations in `cooldowns.yaml` | open |
| H5 / S4 | Write golden questions | open |
| H6 | Set eval thresholds | open |
| H7 | Update external engineering-principles doc | open |
