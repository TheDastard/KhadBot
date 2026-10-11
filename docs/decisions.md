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
  `config/registries/cooldowns.yaml` and `enemy_cast_ids` from every encounter file under `config/encounters/`
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
  ingest filter still uses every difficulty's ability IDs. Plan section 6.2 needs the same edit.
- **M2.6 (legacy comparison) is blocked on H2**: it compares against real fixture pulls, which do not exist yet.
- **M2 exit criteria waiting on humans**: H3 encounter file (<10% unregistered on real reports), real-fixture digest
  goldens and the 1.5k-3k token check (H2), perf budgets (`-m perf`, written, skipped until H2), and M2.6.

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
| H2 / S1 | Record real fixtures, run cost spike | done 2026-10-11: all 62 boss pulls ingested into `KHADBOT_DATA_DIR` (936 points); seven representative pulls copied to `tests/fixtures/wcl/` and stored in Git LFS (see its README); measurements in `docs/wcl-api.md`. |
| H3 / S3 | Author the encounter file for the H1 boss | first pass 2026-10-11, awaiting human review: `config/encounters/53-the_venomous_abyss/3492-ulatek.yaml`. Mechanics from the in-game encounter journal (Wowhead NPC page) and the Method, Wowhead and MythicTrap Heroic guides (Wipefest's Guide tab embeds MythicTrap's guide; Wipefest's own mechanic tagging is not used); every ability ID taken from the recorded pulls. Phases use `wcl_phase` triggers. Across all 47 recorded Ula'tek pulls: coverage `full`, unregistered damage at most 3.1% (all player self-damage such as Spirit Link and Burning Rush), no `phase_trigger_unmatched`, re-annotating every pull takes about 17 s. Deadly Venom (venom pools), Toxin Cloud (early step onto the stage-two platforms) and Ingested Venom (Serpent's Bite soak debuff, believed) identified by the raid. To review: the failure rules, and whether Spectral Coils should also be a damage window. Mother's Wrath (raid), Unchecked Rage, Rattler Slam, Calcified Corpse, Circling Prey, Mephitic Thrash and Desperate Thrash have no failure rule yet because the current rules can only blame the player who was hit. |
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
  later phases. Contract change (plan 6.1 `Pull`, 6.4 digest).
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

Things raised in review that are not tasks in the plan yet.

- **Merge the stacked branches before M5.** `encounter/m0-m1-groundwork-data-layer` → `m2-annotation-digest` →
  `m3-analyst-loop` → `m4-findings-fanout` are unmerged; `main` has none of the encounter work. Merge the stack
  (in order, or `m4-findings-fanout` once) before starting M5.
- **Code changes for the real-report contract** (plan v0.3.3, section 6): add `Pull.boss_hp_pct` and
  `PullDigest.boss_hp_pct` (request `bossPercentage` in `GET_REPORT_META`), take `phase_reached` and the
  `phase_trigger_unmatched` check from the last phase transition instead of `last_phase`, and fix the
  `start_ms` / `end_ms` comments (milliseconds from report start). `raid_size` already matches.
- **Exit checks that real fixtures now unblock**: the legacy comparison (M2.6) and digest goldens for the real
  fixture pulls. The perf tests and the query-tool p95 now pass (see the event store spike above).
- **Golden questions need real refs.** `expected_refs_any` can only be written from digests of ingested real
  pulls; H5 can use the fixture pulls and the full nights in `KHADBOT_DATA_DIR`.
- **Enemy-Casts filter changes after H3.** The real fixtures were recorded with enemy Casts unfiltered (no encounter
  file). Once `config/encounters/53-the_venomous_abyss/3492-ulatek.yaml` exists, the CLI derives a filter and an offline ingest of these
  fixtures misses that one data type. Tests should pass explicit IDs (as for SYNTH01), or the fixtures should be
  re-recorded after H3.
- **Enemy casts stop before the enrage.** `H7z1fqDXjRTFnbtg:15` (the enrage fixture) has no enemy casts after
  574 s of a 618 s pull, while *Fury Unleashed* damage continues. Find out whether the boss casts nothing during
  the enrage or the log lost them before relying on enemy casts near the end of a pull.
- **Git LFS budget.** The real fixtures are about 200 MB. On GitHub's free tier LFS storage and bandwidth are each
  1 GB (bandwidth per month), so every full clone or CI checkout with LFS spends about 200 MB. Jobs that don't need
  real fixtures can set `GIT_LFS_SKIP_SMUDGE=1`.

Resolved 2026-10-11: real fixtures go in Git LFS (`tests/fixtures/wcl/.gitattributes`) and pre-commit skips them;
the `refs/original` backups from the history rewrite were deleted; the leftover pre-commit `commit-msg` hook was
uninstalled. The 12 skipped unit tests are the 12 perf tests, skipped by marker; none skip for a missing
fixture.
