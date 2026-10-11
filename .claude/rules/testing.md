---
paths:
  - "tests/**"
---

# Tests

- Layout: `tests/unit/` (no network, no LLM), `tests/integration/` (real LLM backend: Ollama by default, Groq via `LLM_PROVIDER=groq`), `tests/eval/` (golden questions, Anthropic, run deliberately). Mirror the source path: `src/khadbot/encounter/digest.py` → `tests/unit/encounter/test_digest.py`.
- `asyncio_mode = "auto"`: write `async def test_...` without the asyncio marker.
- Shared fixtures go in `tests/conftest.py`; payload data in `tests/fixtures/`; reusable helpers in `tests/helpers/`.
- HTTP: mock with `respx`. Never hit Raider.IO, WCL, or LLM APIs from a unit test.
- LLMs: patch `khadbot.llm_factory.get_llm` with a callable that accepts `(provider=None, *, role=None)`, so one test can script the analyst and conversation roles separately. Use the tool-capable fake in `tests/helpers/fake_llm.py` for tool-calling scenarios.
- Config: call `reset_config()` after `monkeypatch.setenv` so `get_config()` re-reads the environment.
- Agent YAML: load fixture configs with `load_agent_config(config_root=tmp_path, tools=None)` instead of the cached `get_agent_config()`.
- Golden snapshots: compare against files under `tests/golden/`. Regenerate only with the explicit update option, and review the diff before committing it.
- Perf tests are marked `@pytest.mark.perf` and are skipped by default.
- Don't weaken or delete an assertion to make a test pass. If the expected behaviour changed, say so and update the test deliberately.
- New code ships with tests for the success path, each documented failure path, and the edge cases its design calls out.
