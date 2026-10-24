# KhadBot

Agentic World of Warcraft assistant. It answers questions about a player's character and raid performance using Raider.IO, WarcraftLogs (WCL), SimulationCraft and guide data, and it grounds every claim in that data. Single maintainer; Windows is the primary platform.

## Architecture

KhadBot has two modes. The user picks one explicitly with `/mode`; there is no LLM router.

- **coach**: a conversational performance coach built with `langchain.agents.create_agent`. Its base prompt, tool subset and personas are declared in `config/agents/coach.yaml`.
- **encounter**: encounter review over structured WCL data. The pipeline:
  1. **Ingest** fetches paginated WCL events into a raw page cache, then normalizes them into one parquet file per pull.
  2. **Annotate** tags events using an encounter file (phases, mechanics, failure rules, damage windows) and the defensive cooldown registry.
  3. **Digest** builds a deterministic summary per pull.
  4. **Analyst**, a LangGraph subgraph, investigates through read-only query tools and submits findings with evidence refs. A verify step rejects any finding whose refs don't resolve or whose numbers don't match the records.
  5. **Findings** are cached per pull, keyed by data, config and prompt hashes.
  6. **Conversation**, the user-facing agent, answers from findings and asks the analyst for more when needed.

  A raid plan can optionally be parsed, confirmed by the user, and scored against pulls.

KhadBot runs in one of two deployment modes, chosen by configuration and unrelated to `/mode`:

- **Local mode** (the default) is the CLI on one machine. It needs no Postgres, object storage or Docker: blobs are files under `KHADBOT_DATA_DIR`, findings are SQLite, there is no auth.
- **Hosted mode** (`KHADBOT_MODE=hosted`, with `DATABASE_URL` and the `KHADBOT_S3_*` settings) keeps findings and read models in Postgres and blobs in an S3-compatible bucket.

Every store has a local and a hosted implementation behind one interface, and `khadbot/wiring.py`, the composition root, picks them. Nothing below the entry points reads the mode.

`session.py` holds the `Session` (mode, thread ID, persona) and `run_turn()`, which sends each message to the right mode. The CLI is UI only: it calls `run_turn` and registered command handlers.

## Layout

```
src/khadbot/
  main.py            # entry point: cli, ingest, digest, serve, migrate subcommands
  wiring.py          # composition root: builds the stores for local or hosted mode
  errors.py          # exceptions more than one layer raises or catches (ConfigurationError)
  session.py         # Mode, Session, run_turn()
  config.py          # frozen dataclasses read from env; get_config() / reset_config()
  llm_factory.py     # get_llm(provider=None, *, role=None)
  agent/             # coach agent, agent/persona YAML loading, prompt assembly
    tools/           # the agent's tool registry (empty until tools over cached data return)
  encounter/         # models, config_files, registry_candidates, store, digest, evidence, findings, records_store;
                     # conversation and plan to come
    ingest/          # WCL queries, paged fetch and raw cache, normalize, ingest_report, offline recorded ingest
    annotate/        # annotated frame, phases, failure rules and blame, annotate(), annotation cache
    analysis/        # deaths, mechanics, cooldowns, aggro
    analyst/         # query tools, prompts, verify rules, versioning, analyst graph, review graph
  storage/           # BlobStore protocol: local directory, S3-compatible bucket, zstd-compressing wrapper
  db/                # hosted mode Postgres: pool, migration runner and migrations, findings store, read models
  jobs/              # job specs and functions (ingest, process, review), progress, token resolution
  auth/              # hosted sign-in: WCL OAuth routes, sessions, encrypted WCL tokens, report access (can_read)
  wcl/               # the shared WarcraftLogs client
  simc/              # SimC generated client data: effect codes, table parser, pinned download
  cli/               # Rich + prompt_toolkit REPL
    commands/        # slash-command handlers per mode
  api/               # read-only dashboard API (FastAPI): create_app, /v1 routes, schemas, night summary
config/
  agents/            # coach.yaml, encounter.yaml
  personas/          # khadgar, thrall, xalatath
  templates/         # Jinja system-prompt template
  encounters/        # <zone_id>-<zone_slug>/<encounter_id>-<encounter_slug>.yaml, entries per difficulty
  registries/        # defensive_cooldowns.yaml, friendly_damage.yaml
contracts/           # openapi.json generated from the API (the frontend's types come from it)
scripts/             # dev/ (setup, clean, distclean, contract generator), data/ (fixture data builder, WCL fixture
                     # recorder, registry builder), wcl/ (budget and merged-filter probes, cost spike)
apps/                # TypeScript apps (pnpm workspace): web/ now; desktop/ and discord/ later. Not created yet
packages/            # api-client/: the client generated from contracts/openapi.json. Not created yet
deploy/              # Dockerfile, compose stack and its env file; docs/hosting.md describes running and configuring them
tests/               # unit/, integration/, eval/, fixtures/, golden/, helpers/
docs/decisions.md    # design decisions and their reasons
docs/encounter-pipeline.md  # encounter data stages: what each stores, reads and invalidates
docs/follow-ups.md   # open work: encounter system and per-encounter gaps
```

Runtime data lives under `KHADBOT_DATA_DIR` (default `.khadbot_data/`, gitignored): raw WCL pages, parquet events and per-pull position tables, annotations, persisted pull records, and the findings and checkpoint SQLite databases. Hosted mode (`KHADBOT_MODE=hosted`, `KHADBOT_STORAGE=s3`) keeps the same objects in an S3-compatible bucket and findings and read models in Postgres.

## Design reference

- The design and its data contracts come from the "KhadBot Encounter Review Refactor" plan: https://claude.ai/artifact/Q45H17JuH5EXK8oz17CzQE (read it with the Artifact tool). Section 6 defines the models, file formats, evidence refs and verify rules.
- `docs/decisions.md` records each design decision and why it was made. When you change a decision, update that file in the same change.
- `docs/follow-ups.md` tracks open work that is not a plan task yet. Add a gap you are not fixing; remove it in the commit that fixes it.
- Path-scoped rules in `.claude/rules/` add detail for the area you are working in.

## Commands

Tasks live in `mise.toml` and run the same from cmd, PowerShell and Git Bash; `mise tasks` lists them all. Each runs
the uv command beside it, which also works on its own. Variants of one task share a `group:` prefix (`test:unit`,
`install:cli`); name new tasks the same way.

A new machine or clone runs `scripts/dev/setup.ps1` once (tools, PATH, LFS, dependencies, hooks, `.env`). After that
`uv run` keeps `.venv` in sync, so the install tasks are only for a fresh or lighter `.venv`. New worktrees need no
`mise trust`.

| Task | mise | uv |
|---|---|---|
| Install everything | `mise run install` | `uv sync --extra dev` |
| Lint + typecheck + layer rule + unit tests (gate for every commit) | `mise run check` | `uv run ruff check src tests scripts && uv run mypy src && uv run lint-imports && uv run pytest tests/unit` |
| Layer rule only | `mise run lint:imports` | `uv run lint-imports` |
| Unit tests | `mise run test:unit` | `uv run pytest tests/unit` |
| Integration tests (needs Ollama or Groq) | `mise run test:integ` | `uv run pytest tests/integration` |
| Golden evals (Anthropic; costs money) | `mise run test:eval` | `uv run pytest tests/eval -m eval -s --no-cov` |
| Format | `mise run format` | `uv run ruff format src tests scripts && uv run ruff check --fix src tests scripts` |
| Run the CLI | `mise run cli` | `uv run python main.py cli` |
| Remove caches and build output | `mise run clean` | `scripts/dev/clean.ps1` |
| Same, then delete `.venv` | `mise run clean:all` | `scripts/dev/clean.ps1`, then `scripts/dev/distclean.ps1` |

Add `--no-cov` to pytest for faster local runs; coverage is on by default in `addopts`.

## Gotchas

- **Warnings are errors.** `pyproject.toml` turns `DeprecationWarning` and `PendingDeprecationWarning` into errors, so a new dependency that warns will fail the suite. Fix the cause, or add a narrow `ignore` entry with a comment naming the library.
- **One LLM patch point.** Tests replace `khadbot.llm_factory.get_llm`. Always call it through the module (`llm_factory.get_llm(role=...)`), never `from khadbot.llm_factory import get_llm` at module top, or the patch won't reach your code.
- **Fake models and tools.** `GenericFakeChatModel` has no `bind_tools`. Use the tool-capable fake in `tests/helpers/fake_llm.py`.
- **Python** is 3.14 in `.venv`, pinned by `.python-version`; `requires-python = ">=3.12,<3.15"`; ruff and mypy target 3.12. Don't use syntax newer than 3.12.
- **uv owns `.venv`.** Don't add Python to `mise.toml`; mise only pins Node and pnpm and runs tasks.
- **Package name** is `khadbot`, all lowercase, on disk and in git. Windows ignores case but Linux and CI don't, so never create a differently cased path.
- **Git hooks are shared by every worktree.** `pre-commit install` writes the path of the Python that ran it into `.git/hooks`, so run it only from the main checkout; installed from a worktree, the hooks break for everyone once that worktree is removed. `scripts/dev/setup.ps1` installs them from the main checkout and only checks them in a worktree.
- **Worktrees have no `.env`.** Tests still pass in one because `load_dotenv` walks up to the main checkout's `.env`; CI has none and sets placeholder WCL credentials instead. Never copy `.env` into a worktree.
- **CI action tags.** Some actions publish no major-version tag (`astral-sh/setup-uv@v10` doesn't resolve); pin those to a full release. A job failing at "Set up job" usually means an action reference didn't resolve.

## Code conventions

- Layers: each package imports only the packages below it, enforced by `import-linter` (`mise run lint:imports`, part of `mise run check`) with no exceptions listed. From the top: `main`; `api` and `cli`; `jobs` and `agent`; `wiring`; `db`; `encounter`; `wcl` and `simc`; then `config`, `errors`, `storage` and `llm_factory`. `encounter` defines the interfaces `db` implements; only `wiring` and the entry points choose between local and hosted. A new top-level package is added to the contract in `pyproject.toml` in the same commit.
- Optional dependency groups: core holds what local ingest, digest and the analyst need; `cli`, `api` (FastAPI, uvicorn), `hosted` (Postgres, S3, zstd) and `providers` (OpenAI, Groq, Ollama) are extras, and `dev` includes them all. Import an optional library only on the path that needs it, and when it is missing raise an error that names the group (`errors.missing_group_message`). Add one with `uv add --optional <group>`.
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

Never invent spell IDs, cooldown durations, boss mechanics or WCL API fields. Take them from recorded fixtures, existing config, the user, or (for player spells) game client data via `scripts/data/build_registries.py`. If a value is unknown, use `null` with `verified: false`, or ask.

## Git

Commit rules are in `.claude/rules/git-commits.md`. In short: `[KB][<tag>] <Imperative summary>` with a lowercase system or module tag from that file, a body explaining what and why, no Claude attribution, no plan-stage IDs. Commit or push only when asked.

Each task gets its own branch and worktree off `main`; `main` stays linear and only fast-forwards to a branch whose CI is green, and the branch and worktree are deleted afterwards. Trivial doc and comment fixes may go straight to `main`. `/land` (`.claude/skills/land/`) runs the landing sequence. Details are under "Branches and merging" in the same rules file.

Work split across subagents follows `.claude/rules/parallel-work.md`. In short: the orchestrator stays in the main checkout (a session inside a worktree pins its subagents there), creates each task's worktree itself with `git worktree add`, has each subagent work there by absolute path instead of `EnterWorktree`, and after landing removes every worktree and branch it created.
