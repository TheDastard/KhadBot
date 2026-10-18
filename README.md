# KhadBot — Agentic WoW Assistant

An agentic AI assistant for World of Warcraft players. Ask natural-language questions about your character and your raid/dungeon performance and get advice grounded in real data (Raider.IO, WarcraftLogs, SimulationCraft, guides) rather than generic tips.

KhadBot has two modes, chosen explicitly by the user with `/mode`:

| Mode | Purpose |
|---|---|
| `coach` | Conversational performance coach: character progression, logs, sims and guides. |
| `encounter` | Encounter review: ingests a WarcraftLogs report into a local event store, annotates it with a hand-authored encounter file, builds deterministic per-pull digests, and answers questions through an analyst that cites evidence for every claim. Optionally scores pulls against a raid plan. |

---

## Requirements

Windows is the primary platform. `scripts/setup.ps1` installs and configures everything below (see [Setup](#setup));
this section is what it does, for reference or a manual install.

| Tool | Why | Install |
|---|---|---|
| [Git](https://git-scm.com/) with Git LFS | The recorded WCL fixtures are stored in Git LFS | `winget install Git.Git` (includes LFS) |
| [uv](https://docs.astral.sh/uv/) (0.13 or later) | Python version, virtual environment and dependency management | `winget install astral-sh.uv` |
| [mise](https://mise.jdx.dev/) | Pins Node and pnpm, and runs every project task (`mise run <task>`) | `winget install jdx.mise` |
| [GitHub CLI](https://cli.github.com/) (optional) | Lets `/land` watch CI with `gh`; it falls back to the public API | `winget install GitHub.cli` |

You do not need to install Python yourself: uv provides the version pinned in `.python-version` (the project supports
Python 3.12 to 3.14). Each tool owns one job: uv owns Python and `.venv`, mise owns Node, pnpm and the tasks. Never add
Python to `mise.toml`.

### One-time mise setup

Add `%LOCALAPPDATA%\mise\shims` to your user `PATH` (`scripts/setup.ps1` does this), so `node` and `pnpm` resolve in
every shell, including editors and tools that don't run your shell profile. `mise run` finds them without it.

The project's `mise.toml` needs no `mise trust`: tool pins and tasks work in an untrusted checkout, so every worktree
works as soon as it's created.

## Setup

```powershell
# 1. Clone and enter the project
git clone <repo-url> KhadBot
cd KhadBot

# 2. Install the tools and dependencies (add -WithGitHubCli for gh)
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/setup.ps1

# 3. Configure your LLM provider and API keys in .env (the script creates it from .env.example)
# Set LLM_PROVIDER, the matching API key, and WARCRAFTLOGS_CLIENT_ID/SECRET
```

The script skips anything already in place, so it is safe to rerun. It installs Git, uv and mise with winget if
they're missing, puts mise's shims on your user `PATH`, downloads the LFS fixtures, runs `mise install` and
`mise run install`, installs the pre-commit hooks, and creates `.env` if there isn't one. In a worktree it only checks
the hooks: every worktree shares the main clone's hooks, so they're installed from the main checkout. `-SkipInstall`
only checks and configures, and lists anything still missing. Open a new shell afterwards so the `PATH` change applies.

To do it by hand instead: install the tools above, follow the one-time mise setup, then run `git lfs pull`,
`mise install`, `mise run install`, `uv run pre-commit install` and `copy .env.example .env`.

After setup you rarely need to install anything by hand: `uv run`, which every task uses, syncs `.venv` with
`uv.lock` first. The install tasks are for a fresh `.venv` or a lighter one:

| Command | Installs |
|---|---|
| `mise run install` | Runtime + CLI + dev tools (pytest, ruff, mypy, pre-commit) |
| `mise run install:cli` | Runtime + CLI (`rich`, `prompt_toolkit`) |
| `mise run install:runtime` | Runtime dependencies only |

Run `mise tasks` to list every task. Variants of one task share a prefix (`install:cli`, `test:unit`), and
`mise run "test:*"` runs every task in a group.

## Run the CLI

```powershell
mise run cli
```

The CLI starts in `coach` mode. Commands: `/help`, `/mode coach|encounter`, `/ingest <report>`, `/digest <pull>`, `/clear`, `/exit`.

Example questions:

- `Why is my DPS low? Check Thralladin on area-52 and look at report aAbBcC123456`
- `What does Icy Veins say about the Ret Paladin opener?`
- `Run a sim for my character and compare the Treacherous Transmitter`
- (encounter mode) `What killed us on the Phase 2 wipes, and were raid cooldowns covering the big damage?`

## Encounter review

1. **Ingest** — `/ingest <report>` fetches events from WarcraftLogs (paginated, cached, idempotent) and stores one normalized parquet file per pull under `KHADBOT_DATA_DIR` (default `.khadbot_data/`, gitignored).
2. **Annotate** — events are tagged using the encounter file in `config/encounters/<zone_id>-<zone_slug>/<encounter_id>-<encounter_slug>.yaml` (phases, mechanics, failure rules, damage windows, each listing the difficulties it applies to) and the cooldown registry in `config/registries/defensive_cooldowns.yaml`.
3. **Digest** — each pull gets a deterministic digest: deaths, mechanic failures, cooldown usage, window coverage, friendly damage (classed by the friendly-damage registry in `config/registries/friendly_damage.yaml`, with self-inflicted damage per player), and anomaly flags. Encounter and registry files are authored by hand; unknown values stay `null` rather than guessed.
4. **Analyze** — an analyst agent queries the stored data with read-only tools and submits findings. Every finding carries evidence refs, and numbers in a claim are checked against the referenced records before it is accepted. Findings are cached per pull.
5. **Converse** — a conversation agent answers from findings and dispatches deeper analysis when needed.

## Development

| Command | What it does |
|---|---|
| `mise run check` | All read-only gates: lint + typecheck + unit tests. Run before every commit. |
| `mise run test` | Every test suite |
| `mise run test:unit` | Unit tests only (no LLM inference, no network) |
| `mise run test:integ` | Integration tests; needs a running LLM backend (Ollama by default, or `LLM_PROVIDER=groq`) |
| `mise run test:eval` | Golden-question eval via LangSmith (Anthropic only, expensive) |
| `mise run lint` / `mise run format` / `mise run typecheck` | ruff check, ruff format + fix, mypy |
| `mise run clean` | Remove caches and build artifacts. Leaves `.venv`, `.env` and `.khadbot_data/` alone. |
| `mise run clean:all` | `clean` plus delete `.venv`. Stops processes running from the venv (e.g. a `ruff.exe` held open by the editor) and retries; if files stay locked it lists them. |

Tasks are plain `uv` commands, so they run the same from cmd, PowerShell and Git Bash, and each one also works
without mise (see `mise.toml`). Set environment variables in your shell before the task, for example
`LLM_PROVIDER=groq mise run test:integ` from Git Bash.

Pre-commit hooks (ruff, whitespace, YAML) are configured in `.pre-commit-config.yaml`; the setup script enables them, or run `uv run pre-commit install` from the main checkout (never a worktree: the hooks are shared and record the Python that installed them).

## Project Structure

```
KhadBot/
├── src/khadbot/
│   ├── agent/             # coach agent: YAML-driven config, prompt assembly, personas
│   ├── encounter/         # encounter review: ingest, store, annotate, digest, analyst, findings, plans
│   ├── cli/               # Rich + prompt_toolkit REPL
│   ├── tools/             # LangChain tools: Raider.IO, WarcraftLogs, SimC, guide RAG
│   ├── session.py         # mode dispatch (coach | encounter)
│   ├── config.py          # frozen dataclass config read from the environment
│   └── llm_factory.py     # provider factory (anthropic | openai | groq | ollama), role-aware
├── config/
│   ├── agents/            # coach.yaml, encounter.yaml: base prompts, tool subsets, personas
│   ├── encounters/        # one authored file per encounter, grouped by zone
│   ├── registries/        # cooldown and friendly-damage registries
│   ├── personas/          # khadgar, thrall, xalatath
│   └── templates/         # Jinja prompt template
├── scripts/               # setup.ps1, clean.ps1, distclean.ps1, WarcraftLogs fixture recording
├── tests/                 # unit/, integration/, eval/, fixtures/, golden/
├── main.py                # CLI entrypoint (mise run cli)
├── mise.toml              # Node and pnpm versions, project tasks
├── .python-version        # Python version uv uses for .venv
└── pyproject.toml         # dependencies (managed with uv)
```

## LLM Providers

Set in `.env`. Each provider has a default model; override it with the `*_MODEL` variables.

```
# Ollama (default; local, free)
LLM_PROVIDER=ollama
OLLAMA_MODEL=qwen3:8b

# Anthropic
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-...

# OpenAI
LLM_PROVIDER=openai
OPENAI_API_KEY=sk-...

# Groq (free tier)
LLM_PROVIDER=groq
GROQ_API_KEY=...
```

The analyst and conversation roles can use different models:

```
LLM_PROVIDER_ANALYST=anthropic
ANALYST_MODEL=...
LLM_PROVIDER_CONVERSATION=groq
CONVERSATION_MODEL=...
```
