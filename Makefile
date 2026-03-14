# Makefile — Windows (VSCode terminal)
# Assumes: Python 3.12+ on PATH, GNU make installed via Chocolatey or Scoop
# WARNING: Do NOT use Cygwin make — it runs under /bin/sh and ignores the
#          cmd.exe SHELL override, causing recipe failures. Use Chocolatey make.
# Recommended terminal: PowerShell or Git Bash via VSCode integrated terminal
#
# First time setup:
#   1. make install-dev
#   2. Select LLM provider and model in .env and fill in your API keys
#   3. Select the .venv interpreter in VSCode (Ctrl+Shift+P > Python: Select Interpreter)

.DEFAULT_GOAL := help
.PHONY: help install install-dev bot cli ingest test test-unit test-integ \
        test-eval lint format typecheck clean

# ── Shell ───────────────────────────────────────────────────────────────────
# Force CMD so Windows-native commands (rd, del, set) work correctly.
# Without this, make uses /bin/sh from Git Bash and CMD builtins fail.
SHELL       := cmd.exe
.SHELLFLAGS := /C

# ── Project settings ────────────────────────────────────────────────────────
PYTHON      := python
SRC         := src
TESTS       := tests
VENV        := .venv

# Windows venv binaries live in Scripts\ not bin/
PIP         := $(VENV)\Scripts\pip
PYTEST      := $(VENV)\Scripts\pytest
RUFF        := $(VENV)\Scripts\ruff
MYPY        := $(VENV)\Scripts\mypy
VENV_PYTHON := $(VENV)\Scripts\python


# ── Help ────────────────────────────────────────────────────────────────────
help:
	@echo.
	@echo   KhadBot — Agentic WoW Assistant
	@echo.
	@echo   install        Install runtime dependencies
	@echo   install-dev    Install runtime + dev dependencies
	@echo   bot            Run the Discord bot
	@echo   cli            Run the interactive CLI
	@echo   ingest         Re-ingest Icy Veins spec guides into the vector store
	@echo   test           Run all tests
	@echo   test-unit      Run unit tests only (no LLM inference)
	@echo   test-integ     Run integration tests (requires Ollama or Groq)
	@echo   test-eval      Run LangSmith golden dataset eval (Anthropic only)
	@echo   lint           Lint with ruff
	@echo   format         Auto-format with ruff
	@echo   typecheck      Type-check with mypy
	@echo   clean          Remove caches and build artifacts
	@echo.


# ── Environment ─────────────────────────────────────────────────────────────
install:
	$(PYTHON) -m venv $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -e .

install-dev:
	$(PYTHON) -m venv $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -e ".[dev]"


# ── Entry points ────────────────────────────────────────────────────────────
bot:
	$(VENV_PYTHON) main.py bot

cli:
	$(VENV_PYTHON) main.py cli


# ── RAG ingestion ───────────────────────────────────────────────────────────
# Pass SPECS= to limit ingestion to specific specs, e.g.:
#   make ingest SPECS="fire-mage balance-druid"
SPECS ?=
ingest:
	$(VENV_PYTHON) -m $(SRC).rag.ingest $(SPECS)


# ── Testing ─────────────────────────────────────────────────────────────────
test:
	$(PYTEST) $(TESTS)

test-unit:
	$(PYTEST) $(TESTS)\unit

# Integration tests require a running LLM backend.
# Defaults to Ollama; override with: make test-integ LLM_PROVIDER=groq
test-integ:
	set "LLM_PROVIDER=$(or $(LLM_PROVIDER),ollama)" && $(PYTEST) $(TESTS)\integration

# Eval tests are expensive — run deliberately, not in CI by default.
# Requires ANTHROPIC_API_KEY and LANGSMITH_API_KEY to be set in .env
test-eval:
	set "LLM_PROVIDER=anthropic" && set "LANGCHAIN_TRACING_V2=true" && $(PYTEST) $(TESTS)\eval -s


# ── Code quality ────────────────────────────────────────────────────────────
lint:
	$(RUFF) check $(SRC) $(TESTS)

format:
	$(RUFF) format $(SRC) $(TESTS)
	$(RUFF) check --fix $(SRC) $(TESTS)

typecheck:
	$(MYPY) $(SRC)


# ── Cleanup ─────────────────────────────────────────────────────────────────
clean:
	@if exist $(SRC)\__pycache__ rd /s /q $(SRC)\__pycache__
	@if exist $(TESTS)\__pycache__ rd /s /q $(TESTS)\__pycache__
	@if exist .pytest_cache rd /s /q .pytest_cache
	@if exist .mypy_cache rd /s /q .mypy_cache
	@if exist .ruff_cache rd /s /q .ruff_cache
	@if exist htmlcov rd /s /q htmlcov
	@for /r . %%f in (*.pyc *.pyo) do @del /q "%%f" 2>nul
	@echo Cleaned.
