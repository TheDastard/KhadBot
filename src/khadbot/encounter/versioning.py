"""
encounter/versioning.py

Version stamps that key cached findings, so a behaviour change invalidates them.

- ``prompt_version``: first 12 hex of SHA-256 over a role's prompt templates, its bound tools (name,
  description and the argument schema the model sees) and ``VERIFY_RULES_VERSION``. Tool order does
  not matter; prompt order does.
- ``GRAPH_VERSION``: bumped by hand whenever analyst graph logic changes (budgets, retry, routing).
- ``VERIFY_RULES_VERSION``: bumped whenever a verify rule changes.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence

import orjson
from langchain_core.tools import BaseTool

GRAPH_VERSION = "1"
VERIFY_RULES_VERSION = 1


def tool_fingerprint(tool: BaseTool) -> dict:
    """What the model is told about a tool: its name, description and argument schema."""
    schema = tool.tool_call_schema
    return {
        "name": tool.name,
        "description": tool.description,
        "schema": schema if isinstance(schema, dict) else schema.model_json_schema(),
    }


def prompt_version(
    role: str,
    prompts: Sequence[str],
    tools: Sequence[BaseTool],
    *,
    verify_rules_version: int | None = None,
) -> str:
    """Hash of everything that shapes a role's model behaviour, as 12 hex characters."""
    payload = {
        "role": role,
        "prompts": list(prompts),
        "tools": sorted((tool_fingerprint(t) for t in tools), key=lambda f: f["name"]),
        "verify_rules_version": VERIFY_RULES_VERSION if verify_rules_version is None else verify_rules_version,
    }
    return hashlib.sha256(orjson.dumps(payload, option=orjson.OPT_SORT_KEYS)).hexdigest()[:12]
