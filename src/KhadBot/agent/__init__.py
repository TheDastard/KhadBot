"""
agent/__init__.py

Public surface of the khadbot.agent package.

    from khadbot.agent import build_graph, run_turn

build_graph()  — construct the compiled LangGraph application graph for a session.
run_turn()     — execute one conversational turn against a compiled graph.

Configuration, persona, and prompt assembly utilities live in the config
subpackage:

    from khadbot.agent.config import (
        AgentConfig, get_agent_config,
        get_persona, resolve_session_persona,
        get_assembler,
    )
"""

from khadbot.agent.graph import build_graph, run_turn

__all__ = [
    "build_graph",
    "run_turn",
]
