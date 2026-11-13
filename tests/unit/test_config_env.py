"""
tests/unit/test_config_env.py

The committed environment examples match what config.py reads: every variable config.py reads is listed in
.env.example (set or commented out), and every name in .env.example and deploy/dev.env.example is read by
config.py or by a library that reads it itself. A new setting therefore fails here until it is documented, and a
removed one until its example line goes.
"""

from __future__ import annotations

import ast
from pathlib import Path

from helpers.scripts import load_script

from khadbot import config

check_env = load_script("dev", "check_env")

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "src" / "khadbot" / "config.py"

# config.py's helpers that read one variable named by their first argument.
READERS = {"_require", "_optional", "_csv", "_flag", "_optional_float"}
# Names the examples list that a library reads, not config.py.
LIBRARY_READS = {
    "AWS_ACCESS_KEY_ID": "boto3 (StorageConfig's docstring)",
    "AWS_SECRET_ACCESS_KEY": "boto3 (StorageConfig's docstring)",
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _calls(tree: ast.AST) -> list[tuple[ast.Call, str]]:
    """Every call to a module-level helper, with the name of the function it is written in ('' at module level)."""
    out: list[tuple[ast.Call, str]] = []

    def visit(node: ast.AST, inside: str) -> None:
        for child in ast.iter_child_nodes(node):
            name = child.name if isinstance(child, ast.FunctionDef) else inside
            if isinstance(child, ast.Call) and isinstance(child.func, ast.Name):
                out.append((child, inside))
            visit(child, name)

    visit(tree, "")
    return out


def read_by_config() -> set[str]:
    """The variable names config.py reads."""
    names: set[str] = set()
    for call, inside in _calls(ast.parse(CONFIG.read_text(encoding="utf-8"))):
        func = call.func.id  # type: ignore[attr-defined]
        if func not in READERS | {"_role_env"} or inside in READERS | {"_role_env"}:
            continue  # not a reader, or one helper calling another with a variable key
        first = call.args[0] if call.args else None
        assert isinstance(first, ast.Constant) and isinstance(first.value, str), (
            f"config.py line {call.lineno}: {func}() needs a literal variable name so this test can list it"
        )
        if func == "_role_env":
            suffix = any(
                k.arg == "suffix" and isinstance(k.value, ast.Constant) and k.value.value for k in call.keywords
            )
            for role in config.LLM_ROLES:
                names.add(f"{role.upper()}_{first.value}" if suffix else f"{first.value}_{role.upper()}")
        else:
            names.add(first.value)
    return names


def example_names(relative: str) -> set[str]:
    return set(check_env.env_keys((ROOT / relative).read_text(encoding="utf-8")))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_the_parser_finds_config_reads():
    names = read_by_config()
    assert {"WARCRAFTLOGS_CLIENT_ID", "KHADBOT_MODE", "KHADBOT_WORKER_QUEUES", "LLM_PROVIDER_ANALYST"} <= names
    assert "CONVERSATION_MODEL" in names  # _role_env(..., suffix=True)


def test_env_example_lists_every_variable_config_reads():
    missing = sorted(read_by_config() - example_names(".env.example"))
    assert not missing, f"add these to .env.example (commented out with their default is fine): {missing}"


def test_env_example_lists_nothing_config_does_not_read():
    stale = sorted(example_names(".env.example") - read_by_config() - LIBRARY_READS.keys())
    assert not stale, f".env.example lists names nothing reads (removed, or misspelt?): {stale}"


def test_dev_env_example_lists_nothing_config_does_not_read():
    stale = sorted(example_names("deploy/dev.env.example") - read_by_config() - LIBRARY_READS.keys())
    assert not stale, f"deploy/dev.env.example lists names nothing reads (removed, or misspelt?): {stale}"
