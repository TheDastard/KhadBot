# KhadBot — Agentic WoW Assistant

An agentic AI assistant for World of Warcraft players. Ask natural-language questions about your character and your raid/dungeon performance and get advice grounded in real data (Raider.IO, WarcraftLogs, SimulationCraft, guides) rather than generic tips.

KhadBot has two modes, chosen explicitly by the user with `/mode`:

| Mode | Purpose |
|---|---|
| `coach` | Conversational performance coach: character progression, logs, sims and guides. |
| `encounter` | Encounter review: ingests a WarcraftLogs report into a local event store, annotates it with a hand-authored encounter file, builds deterministic per-pull digests, and answers questions through an analyst that cites evidence for every claim. Optionally scores pulls against a raid plan. |

---

## Requirements

Windows is the primary platform. Two tools are required:

| Tool | Why | Install |
|---|---|---|
| [uv](https://docs.astral.sh/uv/) (0.13 or later) | Python version, virtual environment and dependency management | `winget install astral-sh.uv` |
| [mise](https://mise.jdx.dev/) | Pins Node and pnpm, and runs every project task (`mise run <task>`) | `winget install jdx.mise` |

You do not need to install Python yourself: uv provides the version pinned in `.python-version` (the project supports
Python 3.12 to 3.14). Each tool owns one job: uv owns Python and `.venv`, mise owns Node, pnpm and the tasks. Never add
Python to `mise.toml`.

### One-time mise setup

1. Add `%LOCALAPPDATA%\mise\shims` to your user `PATH`, so `node` and `pnpm` resolve in every shell, including
   editors and tools that don't run your shell profile.
2. Trust the project's `mise.toml` (and every worktree under it) by adding this to
   `%USERPROFILE%\.config\mise\config.toml`, with your clone's path:

   ```toml
   [settings]
   trusted_config_paths = ["D:/Development/Projects/KhadBot"]
   ```

## Setup

```powershell
# 1. Clone and enter the project
git clone <repo-url> KhadBot
cd KhadBot

# 2. Install Node and pnpm, then the Python dependencies (uv creates .venv on first run)
mise install
mise run install

# 3. Configure your LLM provider and API keys
copy .env.example .env
# Edit .env: set LLM_PROVIDER, the matching API key, and WARCRAFTLOGS_CLIENT_ID/SECRET
```

Lighter installs are available if you do not need the dev tooling:

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
2. **Annotate** — events are tagged using the encounter file in `config/encounters/<zone_id>-<zone_slug>/<encounter_id>-<encounter_slug>.yaml` (phases, mechanics, failure rules, damage windows, each listing the difficulties it applies to) and the cooldown registry in `config/registries/cooldowns.yaml`.
3. **Digest** — each pull gets a deterministic digest: deaths, mechanic failures, cooldown usage, window coverage, and anomaly flags. Encounter and registry files are authored by hand; unknown values stay `null` rather than guessed.
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

Pre-commit hooks (ruff, whitespace, YAML) are configured in `.pre-commit-config.yaml`; enable them with `uv run pre-commit install`.

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
│   ├── registries/        # cooldown registry
│   ├── personas/          # khadgar, thrall, xalatath
│   └── templates/         # Jinja prompt template
├── scripts/               # clean.ps1, distclean.ps1, WarcraftLogs fixture recording
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
