"""
agent/__init__.py — ToDo

Public surface:
    from agent import build_agent_executor
    from agent import ask_coach
"""

from khadbot.agent.coach import ask_coach, build_agent_executor

__all__ = [
    "build_agent_executor",
    "ask_coach",
]
