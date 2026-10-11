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
| [uv](https://docs.astral.sh/uv/) | Python version, virtual environment and dependency management | `winget install astral-sh.uv` |
| GNU make (via [Chocolatey](https://chocolatey.org/install)) | Task runner for install, test, lint and cleanup | `choco install make` |

You do not need to install Python yourself — uv provides it (the project requires Python 3.12+).

## Setup

```powershell
# 1. Clone and enter the project
git clone <repo-url> KhadBot
cd KhadBot

# 2. Install dependencies (uv creates .venv on first run)
make install-dev

# 3. Configure your LLM provider and API keys
copy .env.example .env
# Edit .env: set LLM_PROVIDER, the matching API key, and WARCRAFTLOGS_CLIENT_ID/SECRET
```

Lighter installs are available if you do not need the dev tooling:

| Command | Installs |
|---|---|
| `make install` | Runtime dependencies only |
| `make install-cli` | Runtime + CLI (`rich`, `prompt_toolkit`) |
| `make install-dev` | Runtime + CLI + dev tools (pytest, ruff, mypy, pre-commit) |

Run `make` with no arguments to list every target.

## Run the CLI

```powershell
make cli
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
| `make check` | All read-only gates: lint + typecheck + unit tests. Run before every commit. |
| `make test-unit` | Unit tests only (no LLM inference, no network) |
| `make test-integ` | Integration tests; needs a running LLM backend (Ollama by default, or `LLM_PROVIDER=groq`) |
| `make test-eval` | Golden-question eval via LangSmith (Anthropic only, expensive) |
| `make lint` / `make format` / `make typecheck` | ruff check, ruff format + fix, mypy |
| `make clean` | Remove caches and build artifacts |
| `make distclean` | `clean` plus delete `.venv`. Stops processes running from the venv (e.g. a `ruff.exe` held open by the editor) and retries; if files stay locked it lists them. |

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
├── scripts/               # distclean.ps1, WarcraftLogs fixture recording
├── tests/                 # unit/, integration/, eval/, fixtures/, golden/
├── main.py                # CLI entrypoint (make cli)
├── Makefile
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
