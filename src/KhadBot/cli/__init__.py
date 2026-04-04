"""
cli/__init__.py — Rich-powered CLI for the WoW Coaching Agent.

Public surface:
    from cli import run_cli          # main REPL
"""

from khadbot.cli.cli import run_cli

__all__ = [
    "run_cli",
]
