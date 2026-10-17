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
| D8 | Runtime | Python only; `.venv` runs 3.14, pinned by `.python-version`; `requires-python >=3.12,<3.15`; ruff and mypy target 3.12 (updated 2026-10-17, see D12) | Pinned so local, CI and every worktree build the same venv; linting against the oldest supported version keeps newer syntax out | 2026-10-10 |
| D9 | Local persistence | Everything under `KHADBOT_DATA_DIR` (default `.khadbot_data/`); SQLite for findings/plans; `AsyncSqliteSaver` for checkpoints (M5) | One gitignored directory | 2026-10-10 |
| D10 | Package location | One sub-package, `src/khadbot/encounter/`, grouped inside by the module layout rule in CLAUDE.md: a sub-package for three or more modules that form one stage, reached through one or two entry points, with internals nothing else imports | A folder only earns its place when it hides something; shared contracts stay at the parent level | 2026-10-11 |
| D11 | Analyst output | Analyst ends by calling a `submit_report` tool whose schema is `AnalysisReport` | Works across Groq, Ollama, Anthropic and fakes | 2026-10-10 |
| D12 | Tooling | One owner per job. uv owns the Python version (`.python-version`), `.venv` and Python packages, and stays a standalone install with `[tool.uv] required-version` as its floor. pnpm owns `web/node_modules`. mise pins Node and pnpm in `mise.toml` and runs every task (`mise run check` and so on), locally and in CI. The Makefile and Chocolatey `make` are gone. mise never manages Python or the venv: no `python` in `[tools]`, no `_.python.venv`; tasks reach `.venv` only through `uv run`. Tasks are single plain commands with env vars in `env`. Variants of one task share a `group:` prefix (`install:cli`, `test:unit`, `gen:contracts`, `clean:all`); bare `install` is the full dev install. | The Makefile forced `cmd.exe`, so CI and Git Bash couldn't run it, and Node and pnpm had no pin. mise over `just`: `just` is a fine task runner but can't pin Node and pnpm, which would stay a manual install. uv stays outside mise because two tools creating the venv is the failure to avoid, and `uv run` already syncs `.venv` against `uv.lock`. Plain commands behave the same in cmd, PowerShell and Git Bash. | 2026-10-17 |

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
  `config/registries/cooldowns.yaml` and `enemy_cast_ids` from every encounter file under `config/encounters/`
  (`EncounterFile.ability_ids()`: mechanics, damage windows, phase-trigger abilities). A report whose boss has no
  encounter file still fetches enemy `Casts` unfiltered.
- **Cooldown registry kinds (M2.1)**: the 17 ported entries carry a first-pass `kind` (raid / external / personal);
  H4 verifies kinds along with durations. `class` is exposed as `CooldownEntry.class_name`.
- **Unknown keys in authored files are errors** (`extra="forbid"`), so typos fail fast.
- **mypy**: clean on `main` since fdaed26, which also added `types-pyyaml` to dev deps; the encounter work adds no errors.
- **Package casing (M0.1)** was committed on its own, as the plan requires, before the rest of M0.
- **Ingest package (2026-10-11)**: `wcl_queries`, `fetch`, `normalize` and the ingest orchestration (now
  `pipeline.py`) live in `encounter/ingest/`, under the module layout rule in CLAUDE.md. App code imports
  `ingest_report` and `default_store` from the package. `EVENT_SCHEMA` and `empty_events` moved from `normalize`
  to `store`, which owns the schema it persists, so the shared store never imports ingest internals.
- **Analyst package (2026-10-11)**: `analyst.py` (now `graph.py`), `query_tools` and `versioning` live in
  `encounter/analyst/`. App code imports `run_analysis`, `review` and `estimate_review_cost` from the package;
  tests that patch module attributes import `analyst.graph` itself. Tool names, docstrings and argument schemas
  are unchanged, so the prompt version and the findings cache key are too.
- **Analyst modules (2026-10-12)**: `graph.py` was split by concern: `prompts.py` (prompt templates and
  fixed replies, so a prompt edit, which changes the prompt version, is one file in a diff), `verify.py`
  (`check_finding`, `verify_report`; the conversation agent's answer check will reuse them),
  `graph.py` (the single-question analyst and `run_analysis`) and `review_graph.py` (per-pull fan-out,
  findings cache, synthesis, `review` and the cost estimate). The review module is not called
  `review.py` because the package exports a `review` function, which would shadow it. The graph's
  `make_run`, `analysis_config` and `invoke` are public because the review graph uses them. Code and
  prompts moved unchanged: all three prompt versions match the previous layout and `GRAPH_VERSION` stays.
- **Annotate package (2026-10-12)**: `phases.py` and `annotate.py` form `encounter/annotate/`: `frame.py`
  (`Annotated`, the column schema, `is_hit`, `type_is`, `aggro_events`, `ANNOTATION_VERSION`), `failures.py`
  (the failure rules and blame, where the planned stack-count, hit-while-debuffed and add-cause rules go),
  `pipeline.py` (`annotate()`, clusters, wipe cutoff), `cache.py` (`AnnotationCache`, `annotate_pull`) and
  `phases.py`. `frame.py` exists so the failure rules and `annotate()` don't import each other. The core
  module is not `annotate.py` because the package exports an `annotate` function. App code imports from the
  package as before. Code moved unchanged (`_failures` is now the public `mechanic_failures`), so annotated
  frames are identical and `ANNOTATION_VERSION` stays 3.
- **Encounter fixtures (2026-10-12)**: `wcl/`, `wcl_views/`, `encounters/` and `registries/` live under
  `tests/fixtures/encounter/`, next to the coach payload modules in `tests/fixtures/`. `FIXTURES` in
  `tests/helpers/encounter/synth.py` names that encounter root. The Git LFS rules move with `wcl/`, and the
  pre-commit exclude follows the new path.

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
  damage; an external on one player does not cover it. Plan section 6.3 updated to match.
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
- **Encounter file layout and difficulties (2026-10-11)**: files live at
  `config/encounters/<zone_id>-<zone_slug>/<encounter_id>-<encounter_slug>.yaml` (Ula'tek:
  `53-the_venomous_abyss/3492-ulatek.yaml`), so a file says which fight it is without opening it. The zone ID is
  the WCL zone ID from report metadata, the only instance ID the data carries. Slugs are lowercase with apostrophes
  dropped and other runs of non-alphanumerics turned into `_`; a hyphen separates ID from slug because a second dot
  reads as a file extension. The file declares `zone: {id, name}`, and the loader rejects a file whose path doesn't
  match its declared zone, encounter ID and name, and an encounter ID with two files. Every entry (phase, mechanic,
  damage window, ignored ability) has a required `difficulties` list (`lfr`, `normal`, `heroic`, `mythic`; WCL
  codes 1, 3, 4, 5, of which only 4 and 5 are confirmed on recorded reports). `ignore_ability_ids` became
  `ignored_abilities: [{ability_id, name, difficulties}]`. Validation runs per difficulty, so an ID or ability may
  repeat on entries whose difficulties don't overlap (for example a different failure rule on Mythic). Analysis
  uses `load_encounter_view(encounter_id, pull.difficulty)`: only that difficulty's entries, and None (coverage
  `none`) when no phase covers the pull's difficulty or the difficulty is unknown. `encfile_hash` is taken over that
  view, so editing one difficulty's entries leaves the other difficulties' annotations and findings cached. The
  ingest filter still uses every difficulty's ability IDs. Plan section 6.2 updated to match (plan 0.3.4).
- **Failure blame (2026-10-11)**: annotation adds a `blamed_id` column, the player charged with each failure
  row. The existing rules charge the friendly player hit (or the debuffed player for `debuff_duration_gt`);
  `not_interrupted` and `soak_count_lt` charge no one. `MechanicStat.failures_by_player` and
  `MechanicCluster.failed_players` now count `blamed_id`, not the failure row's target, and an `event:` ref on a
  failure row adds `blamed` (a name). Four rules are added so mechanics that punish the raid for one player's
  mistake can be charged to that player:
  - `per_cluster` + `blame`: one failure per cluster, on its first hit. `blame: {by: cast_target, ability_id, ms}`
    charges the friendly target of the latest enemy cast of `ability_id` in the `ms` before;
    `{by: aggro_holder, npc_game_id, ms}` charges the NPC's aggro holder (matched by game ID, since a boss can have
    two actors with one name): the player behind its latest melee swing or taunt in the Threat data in the `ms`
    before (see the Threat entries below). With no match the failure still counts, charged to no one. `blame` is required by `per_cluster` and rejected on every other rule.
  - `hit_amount_gt {value}`: each hit larger than `value` before mitigation (`unmitigated`, else `amount +
    absorbed`), for mechanics that hit everyone but hit hardest those standing in the wrong place.
  - `aura_applied`: each application of one of the mechanic's auras on a player, charged to that player. Like
    `not_interrupted` rows, these are not hits and belong to no cluster.
  - `hits_on_non_tank {ms}`: one failure per player per cluster for players other than the aggro holder of the
    NPC instance that hit them (latest swing or taunt on it in the `ms` before their first hit). Instances matter
    because adds of one kind can share an actor ID (both Weakened Doomscales are one actor), and only the first hit
    is judged because NPCs stop swinging while they cast, so DoT ticks on the tank would otherwise read as failures.
  Annotated frames carry `ANNOTATION_VERSION` (now 3) in their cache sidecar, so frames from before the change
  are rebuilt. The optional `blame` field also changes every encounter file's hash once. Plan sections 6.2
  (rules table) and 6.3 (`failures_by_player`) updated to match (plan 0.3.4).
- **Threat data and NPC instances (2026-10-11)**: ingest fetches a tenth data type, `Threat` (Friendlies side,
  about 1 point per pull; see `docs/wcl-api.md`). WCL Threat has no threat values: it is target tracking, NPC melee
  swings (misses included), taunts and NPC deaths. Normalize keeps the NPC swings, the taunt debuffs on NPCs and
  the NPC deaths, and drops the players' taunt casts, which repeat the Casts data. The event schema gains `source_instance` and
  `target_instance` (WCL `sourceInstance` / `targetInstance`), which number NPCs sharing one actor ID; every
  `events_hash` changes, so all pulls re-ingest from the raw cache. The digest gains `aggro`: per boss NPC, its
  melee swings, the top 3 targets with counts and shares, and the taunts that landed by player. It renders as an
  `AGGRO` section under the pull ref, and the `pull` ref resolves with the same `aggro` list so verify can check
  its numbers. Event rows and death top hits name NPCs with their instance (`Blightscale Rawling #51`).
  Recorded fixtures gained Threat pages and the encounter-filtered enemy Casts. Plan sections 6.1 (FetchSpec,
  event schema), 6.4 (digest) and 6.5 (`pull` ref) updated to match (plan 0.3.4). Blame reads it through
  `aggro_events` (see the blame entries).
- **Aggro bands (2026-10-11)**: `analysis/aggro.py` builds per-NPC-instance bands of who each NPC was attacking:
  a band starts at a swing or taunt on a new player and ends at the next switch, the NPC's death, 20 s with no
  swing or taunt (`AGGRO_QUIET_MS`, ending at the last event), or the pull end. That rule reproduces WCL's own
  threat table: on three pulls from three nights, 140 of 144 bands match to the millisecond, nothing ours calls
  aggro is missing from WCL's, and boss aggro never names a different player. The four differences are WCL
  holding a quiet band longer (see `docs/wcl-api.md`). Two pulls are unit tests against saved WCL tables.
  `aggro_holder(bands, npc_ids, t, instance)` answers who held an NPC at a time. Not used by the digest or
  blame yet.
- **Aggro for blame, reconciled with the Threat data (2026-10-11)**: compared over the 47
  Ula'tek pulls, per raid punishment:
  - The quiet-gap bands are the wrong question for punishments caused by nobody being in melee. The NPC
    stops swinging, so its band has often closed: 8 of 13 Mother's Wraths and 6 of 10 Rattler Slams fall
    outside any band. Blame wants the **last aggro holder**: the latest swing or taunt on that NPC within a
    lookback, with no quiet rule.
  - Mother's Wrath (raid): keep `cast_target`. The cast names the boss's actual target; in both cases where a
    taunt came first, it landed during the 5 s cast and the cast still named the original target.
  - Unchecked Rage: switch `melee_target` (damage hits only) to the last swing or taunt within 30 s. It
    charges `trMgApZCVYkv3jdq:18` at 393.5 s to Shinrael (Growl 2.8 s before) instead of Mekerakesh, and
    `trMgApZCVYkv3jdq:23` at 381.3 s, now charged to no one, to Mekerakesh (taunt 8.8 s before); 7 agree, and
    2 at the start of Stage Three have no holder either way.
  - Rattler Slam: same switch; identical results on these pulls (7 agree, 3 slams before anyone picked up
    the tail have no holder either way). Those unowned cases need the raid plan's tank assignments.
  - Desperate Thrash: judge each cone against the aggro holder of the casting add's instance
    (`source_instance`) instead of any recent melee target of the shared actor. Agrees on 93 of 102 first
    hits; it adds 4 hits on the other add's tank that today's rule misses and drops 1 false failure.
  - Positions would add "was the holder in melee range", which separates a tank out of position from other
    causes; attribution does not need it.
  Implemented: `blame: {by: aggro_holder, npc_game_id, ms}` (the latest swing or taunt on that NPC in the `ms`
  before, from `annotate.aggro_events`; none after the NPC's death) replaces `melee_target`, and
  `hits_on_non_tank` judges each player's first hit per cluster against the aggro holder of the casting NPC
  instance (a hit while the NPC has no holder is a failure). `ANNOTATION_VERSION` is 3. Over the 47 pulls the
  results match the comparison above: Unchecked Rage `trMgApZCVYkv3jdq:18` goes to Shinrael and
  `trMgApZCVYkv3jdq:23` at 381.3 s to Mekerakesh (2 rages now charged to no one, was 3); Mother's Wrath and
  Rattler Slam are unchanged; Desperate Thrash has 28 failures (was 24), the 4 new ones cone hits on the other
  add's tank. That leaves three kinds of failure attribution, each resting on one kind of evidence and testable with a few
  synthetic rows:
  1. **The player hit** (`any_hit`, `hits_per_cluster_gt`, `hit_amount_gt`, `aura_applied`,
     `debuff_duration_gt`): damage or aura rows on the failing player.
  2. **No one** (`not_interrupted`, `soak_count_lt`): an enemy cast or a whole cluster, with no single player
     at fault until raid-plan assignments exist.
  3. **The player responsible for an NPC** (`per_cluster` blame, `hits_on_non_tank`), from the Threat data
     only: `cast_target` (the NPC's own cast names its target) or `aggro_holder` (the NPC's latest swing or
     taunt), and for `hits_on_non_tank` the aggro holder of the casting NPC instance. `melee_target`, which
     read damage hits and missed taunts, is gone.
- **Positions not kept yet (2026-10-11)**: `x`/`y`/`facing` are already in the raw pages, and keeping them would
  grow parquet by 39% and normalize time by about 2 s per night (measurements in `docs/wcl-api.md`). They would
  answer "was the aggro holder in melee range" directly. Waiting on a decision before adding them.
- **Legacy comparison (M2.6, 2026-10-11)**: `docs/legacy-comparison.md`, run with the legacy tool live against the
  seven fixture pulls. Deaths agree exactly (136 of 136); every other difference is a legacy defect. It found four
  defects not in the plan's list (no cooldown is ever matched, pre-death windows hold only self-damage, killing
  blow and overkill always blank, ability-table IDs always 0), and one defect both sides share (the killing ability
  name, in `docs/follow-ups.md`).
- **Real-fixture goldens (2026-10-11)**: `tests/unit/encounter/test_real_fixtures.py` snapshots the first 20
  normalized events and the full digest of every recorded fixture pull (`tests/golden/{normalized,digests}/
  <code>_<fight>.json`), using the encounter files and registry under `config/`, and checks every rendered digest
  stays under 3,000 tokens. It runs in the unit suite (about 10 s) and skips when the Git LFS pages are not pulled.
  An edit to an encounter file or the registry changes these goldens; regenerate with `--update-goldens` and review
  the digest diff. Fixture ingest (`tests/helpers/encounter/real_fixtures.py`, shared with the perf tests) reads enemy Casts
  unfiltered, so editing an encounter file's ability IDs never leaves the fixtures without that data type.
- **M2 exit criteria waiting on humans**: review of the H3 encounter file. Digest goldens, the token check, the perf
  budgets and the legacy comparison are done.

### M3 (analyst loop)

Plan v0.3.2 (2026-10-11) folds the contract changes below into its section 6 and decisions D5/D11.

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
- **Golden eval harness**: question schema, loading and the deterministic checks live in `tests/helpers/encounter/golden.py`
  (unit-tested on SYNTH01); `tests/eval/` runs them on a real analyst model. Eval tests carry an `eval` marker and,
  like `perf`, are skipped unless selected (`-m eval`; `mise run test:eval` passes it). Recorded reports the questions
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

### M4 (findings persistence and fan-out)

- **Started ahead of the M3 gate** (2026-10-11): the M3 exit needs the golden questions, thresholds and the Groq
  gate decision (H5, H6), which are still open. M4 is built and tested on SYNTH01 with fake models. If the gate
  changes the analyst's tools or prompts, the cache key changes with them (by design), but the synthesis prompt
  and the cross-pull golden check should be revisited.
- **What is cached**: only the per-pull review, under `findings.cache_key` (plan 6.8). The synthesis runs on every
  review because it answers the user's question; it is cheap next to the per-pull analyses. So "a second identical
  request makes no model calls" holds for per-pull analysis, not for the synthesis call.
- **Per-pull review question** is the fixed `STANDARD_REVIEW_QUESTION`. It is hashed into the review's
  `prompt_version` (`review_prompt_version()`), which is what the cache key carries; editing it invalidates the
  cache. Synthesis has its own `synthesis_prompt_version()` (role `synthesis`, tools `compare_pulls` and
  `submit_report`), recorded in the final report's `produced_by`.
- **Not cached**: a per-pull report that is `partial` or has `no structured report` is used for this review but
  not stored, so a transient failure is retried next time. The final report is marked `partial` in that case.
- **Flagging** is per stored row: `FindingsStore.flag(finding_id)` flags the `PullFindings` that contains the
  finding. `get` returns flagged rows (with `flagged=True`); the review treats a flagged row as a cache miss,
  re-analyses the pull, and the new row replaces the flagged one under the same key. `for_pull` hides flagged rows
  unless asked.
- **Synthesis wiring**: reuses the analyst graph with `AnalystRun.synthesis` set to the rendered per-pull
  findings, so verify (refs inside the request, numbers match records, one retry) applies unchanged. All
  synthesis findings are forced to `scope="cross_pull"`. If no pull produced findings the model is not called and
  the report carries the pulls' unknowns.
- **Concurrency**: `review` passes `max_concurrency=EncounterConfig.analyst_max_concurrency` in the run config;
  the per-pull `Send` tasks respect it. Per-pull runs are traced as `encounter_analyst` children of
  `encounter_review`.
- **Cost estimate** (`estimate_review_cost`): `sum(len(rendered digest) / 4)` over the pulls plus
  `OUTPUT_TOKENS_PER_PULL` (1,500, an allowance rather than a cap) per pull, with no model call. It ignores
  cached pulls, so it is an upper bound. `/review` prints it before running and `--estimate` prints it only.
  The report code is `--report <code>` (default: the last ingested), because a positional code is ambiguous
  with the first word of the question.

### Event store spike (S2, 2026-10-11)

Outcome: **keep polars + parquet (D7 unchanged).** Measured on the dev machine over all 62 real pulls in
`KHADBOT_DATA_DIR` (three nights, up to 177k events per pull), with the Ula'tek encounter file present for the
47 Ula'tek pulls.

- **Annotate + digest one pull** (budget 2 s): `build_pull_records` cold, median 0.28 s, max 0.45 s
  (`H7z1fqDXjRTFnbtg:18`, 138k events, 26 deaths). Death records are the largest stage (median 135 ms, max
  280 ms: a per-death filter over the frame), then mechanics (53 ms) and annotate (36 ms).
- **Re-annotate a night** (budget 30 s): 0.6 to 1.4 s per night; a full digest of every pull is 3.7 to 7.8 s.
- **Rendered digests** with the encounter file: 1,310 to 2,089 tokens (`len / 4`), inside the 1.5k-3k target
  except the shortest pulls.
- **polars vs DuckDB** on the same 47 pulls, same results asserted for each pair (median / max ms): full parquet
  load 10.5 / 24 vs 34 / 69; filtered read (30 s window, three types) 7.5 / 16 vs 28 / 39; gap clustering
  4.6 / 6.6 vs 50 / 74; aura-interval pairing 1.9 / 4.1 vs 103 / 166; death windows (range join in DuckDB)
  137 / 235 vs 144 / 202. DuckDB is not faster on anything that matters, so there is no reason to switch.
- **Query tool p95** (budget 200 ms) first measured 283 ms to 1.1 s on the longest pull of each night, but not
  because of the store: in `query_events` the window filter took about 1 ms and the rest was building a model
  row for every event in the 30 s window (8k to 22k) before the envelope kept 25. `query_events` now builds rows
  only for the events the envelope can keep and passes the full count as `total`; p95 is now 7 to 77 ms.
- **The `-m perf` tests** ingested the real fixture reports without fight IDs and failed on the fights with no
  recorded pages. They now ingest only the recorded fights; all 12 pass.

## Human task status

| ID | Task | Status |
|----|------|--------|
| H1 | Choose boss + three public reports | done 2026-10-11: Heroic Ula'tek (encounter 3492). Wipe-heavy night `trMgApZCVYkv3jdq`; clean kill `H7z1fqDXjRTFnbtg` fight 20, with the rest of that report as the messy night; second messy / early-learning night `QkVa6GHRK8yfrTbw`. See `docs/wcl-api.md`. |
| H2 / S1 | Record real fixtures, run cost spike | done 2026-10-11: all 62 boss pulls ingested into `KHADBOT_DATA_DIR` (936 points); seven representative pulls copied to `tests/fixtures/encounter/wcl/` and stored in Git LFS (see its README); measurements in `docs/wcl-api.md`. |
| H3 / S3 | Author the encounter file for the H1 boss | first pass 2026-10-11, awaiting human review: `config/encounters/53-the_venomous_abyss/3492-ulatek.yaml`. Mechanics from the in-game encounter journal (Wowhead NPC page) and the Method, Wowhead and MythicTrap Heroic guides (Wipefest's Guide tab embeds MythicTrap's guide; Wipefest's own mechanic tagging is not used); every ability ID taken from the recorded pulls. Phases use `wcl_phase` triggers. Across all 47 recorded Ula'tek pulls: coverage `full`, unregistered damage at most 3.1% (all player self-damage such as Spirit Link and Burning Rush), no `phase_trigger_unmatched`, re-annotating every pull takes about 17 s. Deadly Venom (venom pools), Toxin Cloud (early step onto the stage-two platforms) and Ingested Venom (Serpent's Bite soak debuff, believed) identified by the raid. Raid punishments now use the blame rules: Mother's Wrath (raid) is charged to the cast's target, Unchecked Rage and Rattler Slam to Ula'tek's or Gore Rattle's aggro holder (latest swing or taunt), Calcified Corpse to the bitten player, Circling Prey to hits over 1M before mitigation, and Desperate Thrash to anyone other than the casting add's aggro holder. Across the 47 pulls that gives 13, 11, 10, 19, 12 and 28 failures; 2 Unchecked Rages and 3 Rattler Slams have no aggro holder in the 30 s before and are charged to no one. Mephitic Thrash still has no rule: the logs treat players inside and outside it the same. Open items for this file are under Ula'tek in `docs/follow-ups.md`. |
| H4 | Fill cooldown durations in `cooldowns.yaml` | open |
| H5 / S4 | Write golden questions | open |
| H6 | Set eval thresholds | open |
| H7 | Update external engineering-principles doc | open |

## Findings from the real reports (2026-10-11)

From the fight lists of the three reports and the first live ingest. The API facts behind these are in
`docs/wcl-api.md`; the items below are what they change in the encounter code. Each still needs a code change
and, where marked, a contract update in plan section 6.

- **Raid size for the wipe cutoff stays `len(pull.friendly_player_ids)`.** In all 62 recorded pulls the
  friendly players are exactly the players with events, including the Mythic Nymrissa pulls where WCL reports
  `size=20` but 22 played (Lair fights allow 15 to 25 on Mythic). Never use WCL `size` for anything. Plan 6.3
  should say `raid_size` is the pull's friendly-player count.
- **`lastPhase` is an ordinal, not a phase ID.** WCL numbers stages and intermissions separately, so a pull
  ending in Ula'tek's intermission (phase ID 3) reports `lastPhase=1`. The `phase_trigger_unmatched` anomaly
  compares detected phases with `Pull.last_phase`; switch it to the last `phase_transitions` ID. Phase detection
  itself already uses transitions or triggers.
- **Keep both progress figures.** `Pull.boss_pct` stores `fightPercentage`. Add `bossPercentage` as a separate
  field (`boss_hp_pct`) and keep `fightPercentage` for "how close was the pull"; they differ by 10+ points in
  later phases. Contract change (plan 6.1 `Pull`, 6.4 digest). *Implemented 2026-10-11*: `GET_REPORT_META`
  requests `bossPercentage`; `Pull.boss_hp_pct` and `PullDigest.boss_hp_pct` default to None so `pulls.json`
  written before the change still loads; the `pull` ref, `list_pulls` and `compare_pulls(metric="phase_reached")`
  carry it. The rendered digest header reads `fight 35.0% | boss HP 41.0%` (was `boss 35.0%`, which was fight
  progress). The analyst's multi-pull context and the `list_pulls` / `compare_pulls` docstrings called
  `boss_pct` "boss health"; they now name both figures, which changes the analyst prompt version. Ingest now
  rewrites a pull whose metadata changed even when its `events_hash` did not, so a field added to the report
  query reaches stored pulls. The recorded `report.json` files (fixtures and `KHADBOT_DATA_DIR`) had
  `bossPercentage` merged in from a live `fights { id bossPercentage }` query; nothing else in them changed.
- **Rosters come from each pull.** Report-wide player actors include everyone the logger saw (276 in
  `H7z1fqDXjRTFnbtg`). Anything that lists "the raid" must use the pull's friendly players, never
  `masterData.actors`.
- **A report can mix bosses and difficulties.** `H7z1fqDXjRTFnbtg` has three Heroic bosses and Mythic Nymrissa.
  Encounter-file lookup is per `encounter_id` already; digests and reviews must not assume one boss per report.
- **Phase names.** `report.phases` maps phase IDs to names and intermission flags; the Ula'tek table is in
  `docs/wcl-api.md`. Players count stages, so "phase 3" on Ula'tek is phase ID 4 (Stage Three), which started 362
  to 365 s into every pull that reached it. Use these IDs in `config/encounters/53-the_venomous_abyss/3492-ulatek.yaml`; a `wcl_phase` trigger
  works for every Ula'tek phase. The enrage is *Fury Unleashed* (confirmed by the raid leader).

## Open follow-ups

Moved to `docs/follow-ups.md` (2026-10-11), which tracks open work for the encounter system and for each
encounter file.
