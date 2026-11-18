# Analyst and findings

How the analyst reads records, how verify judges its claims, and what the findings cache keeps. Contracts are in
section 6 of the [design plan](https://claude.ai/artifact/Q45H17JuH5EXK8oz17CzQE).

## Analyst loop

- **Refs resolve against uncapped records**: `digest.build_pull_records` returns every death, mechanic, cooldown and
  window (`PullRecords`) plus the capped digest; `build_digest` wraps it. A ref beyond a digest cap (for example the
  11th death) still resolves, and query tools can return it.
- **`EncounterData`** (`evidence.py`) is the read side for analysis: the store plus the encounter-file root,
  registry, annotation cache and cutoff fraction. It caches `PullRecords` in-process (bounded LRU keyed by
  pull_key, `events_hash` and `encfile_hash`). `resolve(ref, data)` takes `EncounterData` or a bare `EventStore`
  (which then uses `config/` files).
- **What refs resolve to**: `pull` → a summary (outcome, duration, boss %, phase
  reached, cutoff, death counts, coverage, anomalies); `mech:<id>` → the `MechanicStat` plus `cluster_rows`
  (`MechanicCluster`: per-cluster first/last hit, hits, players, damage, failures); `event:<seq>` → the annotated
  event with actor and ability names instead of actor IDs, nulls dropped, `hp_pct` rounded to 0.1. Query tools
  return exactly these dicts, so verify checks claims against what the model saw. `plan:` refs parse but do not
  resolve until raid plans exist.
- **Claim numbers** also accept `2:14` (m:ss), `12.4 seconds`, `250ms`, `1.2k` and `3.4m`; digits inside words
  (`Healbot2`, `p2`, `3rd`) and inside refs are ignored. A number matches if a record value, scaled to the claim's
  unit and rounded to the claim's precision, equals it.
- **Query tool shapes**: rows are the resolved record for their ref, or a
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
- **Verify rules beyond the plan's**: a ref
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
- **Analyst state** includes `forced` (budget hit), `pulls` (requested pulls that are ingested) and `unknowns` (from
  code). Requested pulls that are not ingested become `pull <key> is not ingested` unknowns;
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
  feedback. Validation: `unanswerable`
  requires `must_be_unknown: true`, and answerable questions need `expected_refs_any`. An optional `depth` field
  selects the step budget.

## Findings persistence and fan-out

- **What is cached**: only the per-pull review, under `findings.cache_key`. The synthesis runs on every
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
