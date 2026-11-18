# KhadBot

Agentic World of Warcraft assistant. It answers questions about a player's character and raid performance using Raider.IO, WarcraftLogs (WCL), SimulationCraft and guide data, and it grounds every claim in that data. Single maintainer; Windows is the primary platform.

## Architecture

Two modes, picked by the user with `/mode`; there is no LLM router. `session.py` holds the `Session` and `run_turn()`, which routes each message; the CLI is UI only.

- **coach**: a conversational coach built with `langchain.agents.create_agent`, declared in `config/agents/coach.yaml`.
- **encounter**: review over structured WCL data. Ingest fetches WCL events into a raw page cache and normalizes them to parquet per pull; annotate tags them from an encounter file and the cooldown registries; digest builds a deterministic summary; the analyst (a LangGraph subgraph) investigates through read-only query tools and submits findings with evidence refs, which a verify step checks against the records; findings are cached per pull. `docs/encounter/` describes every stage.

Two deployment modes, chosen by configuration and unrelated to `/mode`:

- **Local** (default): the CLI on one machine. Blobs are files under `KHADBOT_DATA_DIR` (default `.khadbot_data/`), findings are SQLite, no auth, no Docker.
- **Hosted** (`KHADBOT_MODE=hosted`): findings and read models in Postgres, blobs in an S3-compatible bucket, a job queue, WCL sign-in. `docs/hosting/` describes it.

Every store has a local and a hosted implementation behind one interface; `khadbot/wiring.py`, the composition root, picks them. Nothing below the entry points reads the mode.

## Where things are

- `src/khadbot/`: `main.py` (subcommands), `wiring.py`, `session.py`, `config.py`, `llm_factory.py`, and the packages `agent/`, `encounter/` (`ingest/`, `annotate/`, `analysis/`, `analyst/`), `storage/`, `db/`, `jobs/`, `auth/`, `wcl/`, `simc/`, `cli/`, `api/`.
- `config/`: agents, personas, prompt templates, encounter files (`encounters/<zone_id>-<zone_slug>/<encounter_id>-<encounter_slug>.yaml`), registries, model prices.
- `apps/web/` (Next.js dashboard) and `packages/api-client/` (generated from `contracts/openapi.json`) form a pnpm workspace.
- `deploy/` (image, compose stack), `scripts/` (`dev/`, `data/`, `wcl/`), `tests/` (`unit/`, `integration/`, `eval/`, `fixtures/`, `golden/`, `helpers/`).
- Docs: `docs/decisions.md` (decisions and why), `docs/follow-ups.md` (open work), and the reference docs `docs/encounter/`, `docs/hosting/`, `docs/wcl/`, `docs/game-art.md`.

## Design reference

- Contracts come from the [Encounter Review Refactor plan](https://claude.ai/artifact/Q45H17JuH5EXK8oz17CzQE) (read it with the Artifact tool); section 6 defines the models, file formats, evidence refs and verify rules.
- When you make or change a decision, update `docs/decisions.md` in the same change. Add a gap you are not fixing to `docs/follow-ups.md`; delete it in the commit that fixes it.
- `.claude/rules/` holds path-scoped rules for each area; `.claude/rules/docs.md` says how to write and update docs.

## Commands

Tasks live in `mise.toml` and run the same from cmd, PowerShell and Git Bash; `mise tasks` lists them all, each with the uv command it runs. Variants share a `group:` prefix (`test:unit`, `install:cli`). A new machine runs `scripts/dev/setup.ps1` once; after that `uv run` keeps `.venv` in sync.

| Task | Command |
|---|---|
| Gate for every commit: ruff, mypy, layer rule, unit tests | `mise run check` |
| Web gate: api-client drift, ESLint, tsc, Vitest / both gates | `mise run check:web` / `mise run check:all` |
| Unit tests (add `--no-cov` for speed) | `mise run test:unit` or `uv run pytest tests/unit --no-cov` |
| Integration tests (needs Ollama or Groq) / golden evals (Anthropic; costs money) | `mise run test:integ` / `mise run test:eval` |
| Regenerate the API contract, then the client's types | `mise run gen:contracts`, `mise run gen:types` |
| Format | `mise run format` |
| Run the CLI | `mise run cli` |
| Dev loop: Postgres and bucket in Docker, API and worker native with reload, web dev server | `mise run dev:all` |
| Web end-to-end tests (dev loop with SYNTH01 seeded / `main.py serve`) | `mise run test:e2e` / `mise run test:e2e:local` |
| Check `.env` against `.env.example` | `mise run env:check` |

## Gotchas

- **Warnings are errors.** `pyproject.toml` turns `DeprecationWarning` and `PendingDeprecationWarning` into errors, so a new dependency that warns will fail the suite. Fix the cause, or add a narrow `ignore` entry with a comment naming the library.
- **One LLM patch point.** Tests replace `khadbot.llm_factory.get_llm`. Always call it through the module (`llm_factory.get_llm(role=...)`), never `from khadbot.llm_factory import get_llm` at module top, or the patch won't reach your code.
- **Fake models and tools.** `GenericFakeChatModel` has no `bind_tools`. Use the tool-capable fake in `tests/helpers/fake_llm.py`.
- **Python** is 3.14 in `.venv`, pinned by `.python-version`; `requires-python = ">=3.12,<3.15"`; ruff and mypy target 3.12. Don't use syntax newer than 3.12.
- **uv owns `.venv`.** Don't add Python to `mise.toml`; mise only pins Node and pnpm and runs tasks.
- **Package name** is `khadbot`, all lowercase, on disk and in git. Windows ignores case but Linux and CI don't, so never create a differently cased path.
- **Git hooks are shared by every worktree.** `pre-commit install` writes the path of the Python that ran it into `.git/hooks`, so run it only from the main checkout.
- **Worktrees have no `.env`.** Tests still pass in one because `load_dotenv` walks up to the main checkout's `.env`; CI sets placeholder WCL credentials instead. Never copy `.env` into a worktree.
- **CI action tags.** Some actions publish no major-version tag (`astral-sh/setup-uv@v10` doesn't resolve); pin those to a full release. A job failing at "Set up job" usually means an action reference didn't resolve.

## Code conventions

- Layers: each package imports only the packages below it, enforced by `import-linter` with no exceptions. From the top: `main`; `api` and `cli`; `jobs` and `agent`; `wiring`; `db`; `encounter`; `wcl` and `simc`; then `config`, `errors`, `storage` and `llm_factory`. `encounter` defines the interfaces `db` implements. A new top-level package joins the contract in `pyproject.toml` in the same commit.
- Optional dependency groups: core holds what local ingest, digest and the analyst need; `cli`, `api`, `hosted` and `providers` are extras, and `dev` includes them all. Import an optional library only on the path that needs it, and when it is missing raise an error naming the group (`errors.missing_group_message`). Add one with `uv add --optional <group>`.
- Every module starts with a docstring naming its path and purpose, then `from __future__ import annotations`. Separate sections with `# ----` banner comments.
- Google-style docstrings. Ruff line length 120, double quotes.
- Data models use Pydantic v2. App config stays as frozen dataclasses in `config.py`.
- Tool descriptions live in `@tool` docstrings. Treat docstring edits as prompt changes: they change model behaviour and, for encounter tools, the prompt version and findings cache key.
- Config files fail fast: invalid YAML or a missing reference raises `ConfigurationError` at load, not at first use.
- Module layout: make a sub-package when three or more modules form one stage or concern, change together, are reached through one or two entry points, and have internals nothing outside imports. Shared contracts stay at the parent level. Tests mirror the source layout.
- Add dependencies with `uv add` and say why. Never use `pip`.

## Game data

Never invent spell IDs, cooldown durations, boss mechanics or WCL API fields. Take them from recorded fixtures, existing config, the user, or (for player spells) game client data via `scripts/data/build_registries.py`. If a value is unknown, use `null` with `verified: false`, or ask.

## Git

Commit rules are in `.claude/rules/git-commits.md`: `[<tag>][<subtag>] <Imperative summary>` from the registry `scripts/dev/commit_tags.yaml`, a body explaining what and why, no Claude attribution, no plan-stage IDs. Commit or push only when asked.

Each task gets its own branch and worktree off `main`; `main` stays linear and only fast-forwards to a branch whose CI is green. Docs-only changes go straight to `main`. `/land` runs the landing sequence.

Tasks from a plan are executed by subagents, one per task, never by the orchestrator itself. Before spawning them, load the `parallel-work` skill: it covers worktrees, briefs, integration and cleanup.
