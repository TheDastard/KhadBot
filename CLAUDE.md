# KhadBot

Agentic World of Warcraft assistant. It answers questions about a player's character and raid performance using Raider.IO, WarcraftLogs (WCL), SimulationCraft and guide data, and it grounds every claim in that data. Single maintainer; Windows is the primary platform.

## Architecture

KhadBot has two modes. The user picks one explicitly with `/mode`; there is no LLM router.

- **coach**: a conversational performance coach built with `langchain.agents.create_agent`. Its base prompt, tool subset and personas are declared in `config/agents/coach.yaml`.
- **encounter**: encounter review over structured WCL data. The pipeline:
  1. **Ingest** fetches paginated WCL events into a raw page cache, then normalizes them into one parquet file per pull.
  2. **Annotate** tags events using an encounter file (phases, mechanics, failure rules, damage windows) and the cooldown registry.
  3. **Digest** builds a deterministic summary per pull.
  4. **Analyst**, a LangGraph subgraph, investigates through read-only query tools and submits findings with evidence refs. A verify step rejects any finding whose refs don't resolve or whose numbers don't match the records.
  5. **Findings** are cached per pull, keyed by data, config and prompt hashes.
  6. **Conversation**, the user-facing agent, answers from findings and asks the analyst for more when needed.

  A raid plan can optionally be parsed, confirmed by the user, and scored against pulls.

`session.py` holds the `Session` (mode, thread ID, persona) and `run_turn()`, which sends each message to the right mode. The CLI is UI only: it calls `run_turn` and registered command handlers.

## Layout

```
src/khadbot/
  main.py            # entry point: cli, ingest, digest subcommands
  session.py         # Mode, Session, run_turn()
  config.py          # frozen dataclasses read from env; get_config() / reset_config()
  llm_factory.py     # get_llm(provider=None, *, role=None)
  agent/             # coach agent, agent/persona YAML loading, prompt assembly
  encounter/         # models, config_files, store, digest, evidence, findings, CLI commands;
                     # conversation and plan to come
    ingest/          # WCL queries, paged fetch and raw cache, normalize, ingest_report
    annotate/        # annotated frame, phases, failure rules and blame, annotate(), annotation cache
    analysis/        # deaths, mechanics, cooldowns, aggro
    analyst/         # query tools, prompts, verify rules, versioning, analyst graph, review graph
  cli/               # Rich + prompt_toolkit REPL
  tools/             # LangChain @tools and the shared WarcraftLogs client
config/
  agents/            # coach.yaml, encounter.yaml
  personas/          # khadgar, thrall, xalatath
  templates/         # Jinja system-prompt template
  encounters/        # <zone_id>-<zone_slug>/<encounter_id>-<encounter_slug>.yaml, entries per difficulty
  registries/        # cooldowns.yaml
scripts/             # setup, WCL fixture recorder, clean and distclean
tests/               # unit/, integration/, eval/, fixtures/, golden/, helpers/
docs/decisions.md    # design decisions and their reasons
docs/follow-ups.md   # open work: encounter system and per-encounter gaps
```

Runtime data lives under `KHADBOT_DATA_DIR` (default `.khadbot_data/`, gitignored): raw WCL pages, parquet events, annotations, and the findings and checkpoint SQLite databases.

## Design reference

- The design and its data contracts come from the "KhadBot Encounter Review Refactor" plan: https://claude.ai/artifact/Q45H17JuH5EXK8oz17CzQE (read it with the Artifact tool). Section 6 defines the models, file formats, evidence refs and verify rules.
- `docs/decisions.md` records each design decision and why it was made. When you change a decision, update that file in the same change.
- `docs/follow-ups.md` tracks open work that is not a plan task yet. Add a gap you are not fixing; remove it in the commit that fixes it.
- Path-scoped rules in `.claude/rules/` add detail for the area you are working in.

## Commands

Tasks live in `mise.toml` and run the same from cmd, PowerShell and Git Bash; `mise tasks` lists them all. Each runs
the uv command beside it, which also works on its own. Variants of one task share a `group:` prefix (`test:unit`,
`install:cli`); name new tasks the same way.

A new machine or clone runs `scripts/setup.ps1` once (tools, PATH, LFS, dependencies, hooks, `.env`). After that
`uv run` keeps `.venv` in sync, so the install tasks are only for a fresh or lighter `.venv`. New worktrees need no
`mise trust`.

| Task | mise | uv |
|---|---|---|
| Install everything | `mise run install` | `uv sync --extra dev` |
| Lint + typecheck + unit tests (gate for every commit) | `mise run check` | `uv run ruff check src tests && uv run mypy src && uv run pytest tests/unit` |
| Unit tests | `mise run test:unit` | `uv run pytest tests/unit` |
| Integration tests (needs Ollama or Groq) | `mise run test:integ` | `uv run pytest tests/integration` |
| Golden evals (Anthropic; costs money) | `mise run test:eval` | `uv run pytest tests/eval -m eval -s --no-cov` |
| Format | `mise run format` | `uv run ruff format src tests && uv run ruff check --fix src tests` |
| Run the CLI | `mise run cli` | `uv run python main.py cli` |
| Remove caches and build output | `mise run clean` | `scripts/clean.ps1` |
| Same, then delete `.venv` | `mise run clean:all` | `scripts/clean.ps1`, then `scripts/distclean.ps1` |

Add `--no-cov` to pytest for faster local runs; coverage is on by default in `addopts`.

## Gotchas

- **Warnings are errors.** `pyproject.toml` turns `DeprecationWarning` and `PendingDeprecationWarning` into errors, so a new dependency that warns will fail the suite. Fix the cause, or add a narrow `ignore` entry with a comment naming the library.
- **One LLM patch point.** Tests replace `khadbot.llm_factory.get_llm`. Always call it through the module (`llm_factory.get_llm(role=...)`), never `from khadbot.llm_factory import get_llm` at module top, or the patch won't reach your code.
- **Fake models and tools.** `GenericFakeChatModel` has no `bind_tools`. Use the tool-capable fake in `tests/helpers/fake_llm.py`.
- **Python** is 3.14 in `.venv`, pinned by `.python-version`; `requires-python = ">=3.12,<3.15"`; ruff and mypy target 3.12. Don't use syntax newer than 3.12.
- **uv owns `.venv`.** Don't add Python to `mise.toml`; mise only pins Node and pnpm and runs tasks.
- **Package name** is `khadbot`, all lowercase, on disk and in git. Windows ignores case but Linux and CI don't, so never create a differently cased path.
- **Worktrees have no `.env`.** Tests still pass in one because `load_dotenv` walks up to the main checkout's `.env`; CI has none and sets placeholder WCL credentials instead. Never copy `.env` into a worktree.
- **CI action tags.** Some actions publish no major-version tag (`astral-sh/setup-uv@v10` doesn't resolve); pin those to a full release. A job failing at "Set up job" usually means an action reference didn't resolve.

## Code conventions

- Every module starts with a docstring naming its path and purpose, then `from __future__ import annotations`.
- Separate sections with `# ----` banner comments, as existing modules do.
- Google-style docstrings. Ruff line length 120, double quotes.
- Data models use Pydantic v2. App config stays as frozen dataclasses in `config.py`.
- Tool descriptions live in `@tool` docstrings. Treat docstring edits as prompt changes: they change model behaviour and, for encounter tools, the prompt version and findings cache key.
- Config files fail fast: invalid YAML or a missing reference raises `ConfigurationError` at load, not at first use.
- Module layout: make a sub-package when a group of modules forms one stage or concern: three or more modules that
  change together, that the rest of the code reaches through one or two entry points, and whose internals nothing
  outside the group imports. Shared contracts (models, the event store, config loaders) stay at the parent level.
  Below that bar, keep modules in their parent. Tests and test helpers mirror the source layout.
- Add dependencies with `uv add` and say why. Never use `pip`.

## Game data

Never invent spell IDs, cooldown durations, boss mechanics or WCL API fields. Take them from recorded fixtures, existing config, or the user. If a value is unknown, use `null` with `verified: false`, or ask.

## Git

Commit rules are in `.claude/rules/git-commits.md`. In short: `[KB][<tag>] <Imperative summary>` with a lowercase system or module tag from that file, a body explaining what and why, no Claude attribution, no plan-stage IDs. Commit or push only when asked.

Each task gets its own branch and worktree off `main`; `main` stays linear and only fast-forwards to a branch whose CI is green, and the branch and worktree are deleted afterwards. Trivial doc and comment fixes may go straight to `main`. `/land` (`.claude/skills/land/`) runs the landing sequence. Details are under "Branches and merging" in the same rules file.
