"""
tests/unit/encounter/test_versioning.py

prompt_version: stable, 12 hex, and sensitive to prompts, tool docstrings, tool argument schemas,
the role and VERIFY_RULES_VERSION, but not to tool order.
"""

from __future__ import annotations

import re

from langchain_core.tools import tool

from khadbot.encounter import versioning
from khadbot.encounter.query_tools import build_analyst_tools
from khadbot.encounter.versioning import prompt_version


@tool
def alpha(pull: str) -> str:
    """Alpha tool."""
    return pull


@tool
def beta(pull: str) -> str:
    """Beta tool."""
    return pull


@tool("alpha")
def alpha_new_doc(pull: str) -> str:
    """Alpha tool, reworded."""
    return pull


@tool("alpha")
def alpha_new_arg(pull: str, phase: str | None = None) -> str:
    """Alpha tool."""
    return pull


BASE = prompt_version("analyst", ["system prompt"], [alpha, beta])


def test_format_and_stability():
    assert re.fullmatch(r"[0-9a-f]{12}", BASE)
    assert prompt_version("analyst", ["system prompt"], [alpha, beta]) == BASE


def test_tool_order_does_not_matter():
    assert prompt_version("analyst", ["system prompt"], [beta, alpha]) == BASE


def test_prompt_change_changes_hash():
    assert prompt_version("analyst", ["system prompt!"], [alpha, beta]) != BASE
    assert prompt_version("analyst", ["system prompt", "retry"], [alpha, beta]) != BASE


def test_docstring_change_changes_hash():
    assert prompt_version("analyst", ["system prompt"], [alpha_new_doc, beta]) != BASE


def test_argument_change_changes_hash():
    assert prompt_version("analyst", ["system prompt"], [alpha_new_arg, beta]) != BASE


def test_role_changes_hash():
    assert prompt_version("conversation", ["system prompt"], [alpha, beta]) != BASE


def test_verify_rules_version_changes_hash(monkeypatch):
    assert prompt_version("analyst", ["system prompt"], [alpha, beta], verify_rules_version=2) != BASE
    monkeypatch.setattr(versioning, "VERIFY_RULES_VERSION", versioning.VERIFY_RULES_VERSION + 1)
    assert prompt_version("analyst", ["system prompt"], [alpha, beta]) != BASE


def test_real_analyst_tools_hash():
    tools = build_analyst_tools()
    assert prompt_version("analyst", ["p"], tools) == prompt_version("analyst", ["p"], list(reversed(tools)))
