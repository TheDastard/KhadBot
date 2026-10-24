"""
agent/tools/__init__.py — The agent's LangChain tools.

TOOLS is the registry `config/agents/<name>.yaml` declarations resolve against. It is empty: the agent's tools
never call Warcraft Logs, SimulationCraft or Raider.IO themselves. Offline pipelines fetch and cache that data,
and the tools that come back here will query the cached data locally.
"""

from __future__ import annotations

from langchain_core.tools import BaseTool

TOOLS: list[BaseTool] = []

__all__ = ["TOOLS"]
