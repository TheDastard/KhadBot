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
- **CLI ingest before M2**: the registry and encounter files do not exist yet, so `/ingest` and `main.py ingest`
  fetch `Buffs` and enemy `Casts` **unfiltered** until M2 passes the registry/encounter IDs into `ingest_report`.
  Avoid running them against real reports before then (cost); `scripts/spike_wcl_cost.py` measures it.
- **mypy**: `make check` already fails on `main` (7 errors: missing `types-PyYAML`, `tool_panel.py`, `simc.py`,
  `agent_config.py`, `cli.py`, `llm_factory.py` kwargs). M0/M1 add none; they were left alone.
- **Package casing (M0.1)** was committed on its own, as the plan requires, before the rest of M0.

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
