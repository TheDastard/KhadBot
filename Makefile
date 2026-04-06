# Makefile — Windows (VSCode terminal)
# Assumes: uv installed (winget install astral-sh.uv), GNU make installed via Chocolatey
# WARNING: Do NOT use Cygwin make — it runs under /bin/sh and ignores the
#          cmd.exe SHELL override, causing recipe failures. Use Chocolatey make.
# Recommended terminal: PowerShell or Git Bash via VSCode integrated terminal
#
# First time setup:
#   1. make install-dev
#   2. Select LLM provider and model in .env and fill in your API keys
#   3. Select the .venv interpreter in VSCode (Ctrl+Shift+P > Python: Select Interpreter)

.DEFAULT_GOAL := help
.PHONY: help install install-cli install-dev cli check test test-unit \
        test-integ test-eval lint format typecheck clean distclean

# ── Shell ───────────────────────────────────────────────────────────────────
# Force CMD so Windows-native commands (rd, del, set) work correctly.
# Without this, make uses /bin/sh from Git Bash and CMD builtins fail.
SHELL       := cmd.exe
.SHELLFLAGS := /C

# ── Project settings ────────────────────────────────────────────────────────
UV    := uv
SRC   := src
TESTS := tests
VENV  := .venv

PYTEST := $(UV) run pytest
RUFF   := $(UV) run ruff
MYPY   := $(UV) run mypy
PYTHON := $(UV) run python


# ── Help ────────────────────────────────────────────────────────────────────
help:
	@echo.
	@echo   KhadBot — Agentic WoW Assistant
	@echo.
	@echo   Setup
	@echo   install        Install runtime dependencies
	@echo   install-cli    Install runtime + CLI dependencies
	@echo   install-dev    Install runtime + dev dependencies
	@echo.
	@echo   Run
	@echo   cli            Launch the interactive CLI
	@echo.
	@echo   Test
	@echo   check          Run all read-only gates (lint + typecheck + unit tests)
	@echo   test           Run all tests
	@echo   test-unit      Run unit tests only (no LLM inference)
	@echo   test-integ     Run integration tests (requires Ollama or Groq)
	@echo   test-eval      Run LangSmith golden dataset eval (Anthropic only)
	@echo.
	@echo   Code quality
	@echo   lint           Lint with ruff (read-only)
	@echo   format         Auto-format and fix with ruff (writes files)
	@echo   typecheck      Type-check with mypy
	@echo.
	@echo   Cleanup
	@echo   clean          Remove caches and build artifacts
	@echo   distclean      Remove everything including the venv
	@echo.


# ── Environment ─────────────────────────────────────────────────────────────
install: pyproject.toml
	$(UV) sync --no-dev

install-cli: pyproject.toml
	$(UV) sync --no-dev --extra cli

install-dev: pyproject.toml
	$(UV) sync --extra dev


# ── Run ─────────────────────────────────────────────────────────────────────
cli:
	$(PYTHON) main.py cli


# ── Testing ─────────────────────────────────────────────────────────────────
check: lint typecheck test-unit

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

# Note: format writes files — run lint separately for a read-only check.
format:
	$(RUFF) format $(SRC) $(TESTS)
	$(RUFF) check --fix $(SRC) $(TESTS)

typecheck:
	$(MYPY) $(SRC)


# ── Cleanup ─────────────────────────────────────────────────────────────────
clean:
	@if exist $(SRC)\khadbot.egg-info rd /s /q $(SRC)\khadbot.egg-info
	@if exist $(SRC)\__pycache__ rd /s /q $(SRC)\__pycache__
	@if exist $(TESTS)\__pycache__ rd /s /q $(TESTS)\__pycache__
	@if exist .pytest_cache rd /s /q .pytest_cache
	@if exist .mypy_cache rd /s /q .mypy_cache
	@if exist .ruff_cache rd /s /q .ruff_cache
	@if exist htmlcov rd /s /q htmlcov
	@for /r . %%f in (*.pyc *.pyo) do @del /q "%%f" 2>nul
	@echo Cleaned.

distclean: clean
	@if exist $(VENV) rd /s /q $(VENV)
	@echo Removed venv.
