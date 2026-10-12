---
paths:
  - "src/khadbot/encounter/**"
  - "src/khadbot/session.py"
  - "config/encounters/**"
  - "config/registries/**"
  - "config/agents/encounter.yaml"
  - "tests/**/encounter/**"
  - "tests/golden/**"
  - "tests/eval/**"
  - "scripts/record_wcl_fixture.py"
  - "docs/decisions.md"
---

# Encounter review

Contracts (models, file formats, refs, verify rules) are defined in section 6 of the design plan: https://claude.ai/artifact/Q45H17JuH5EXK8oz17CzQE. If you change a contract, update `docs/decisions.md` in the same change and say so, so the plan can be updated too.

## Design

- Mode is chosen by the user with `/mode`. No LLM router.
- Event store: polars + parquet, one file per pull, behind the `EventStore` protocol. Analysis code depends on the protocol, never on polars I/O directly.
- All runtime data lives under `KHADBOT_DATA_DIR`. Findings and plans go in SQLite via stdlib `sqlite3`; conversation checkpoints via `langgraph-checkpoint-sqlite`.
- LLMs come from `llm_factory.get_llm(role="analyst" | "conversation")`, called through the module attribute.
- The analyst ends by calling a `submit_report` tool. Don't use `with_structured_output`.
- `prompt_version` hashes prompts, tool docstrings and argument schemas, and `VERIFY_RULES_VERSION`. `GRAPH_VERSION` is bumped by hand when analyst logic changes. Both feed the findings cache key.

## Invariants

- Counting, timing, tagging and arithmetic are deterministic Python. The LLM picks which queries to run and writes claims; it never computes numbers.
- Every finding carries evidence refs that resolve. Every number in a claim must match a resolved record.
- Analyst tools read only from the store. Neither agent gets a tool that calls WCL directly; ingest is a separate, explicit step.
- Times inside a pull are fight-relative milliseconds (`t_ms`). Report-absolute times appear only on `Pull.start_ms` / `end_ms`.
- Ingest is idempotent (`events_hash`) and resumable (raw page cache). Changing a filter refetches only that data type.
- Query tools return the capped `ToolResult` envelope; nothing returns unbounded rows.
- API keys never enter graph state, messages, checkpoints or traces.
- Unit tests make no network calls. Use the raw page cache fixtures and `respx`.

## Game data

- Spell IDs, mechanic tagging and cooldown values come from recorded fixtures or a human. Unknown values are `null` with `verified: false`.
- The synthetic fixture uses fake spell IDs from 900001 upward and says so in its README.
- Don't copy curated tagging from Wipefest (AGPL-3.0) or WoWAnalyzer into encounter files.
- If a task needs data only a person can provide (real WCL fixtures, an authored encounter file, cooldown durations, golden-question labels, eval thresholds), stop and say what is missing. Don't fabricate it.

## Plan IDs

The plan's IDs (milestones, human tasks, spikes, decisions) are for conversation only. Keep them out of code, comments, docstrings and commit messages.
