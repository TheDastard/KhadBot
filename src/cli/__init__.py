"""
cli/__init__.py — Rich-powered CLI for the WoW Coaching Agent.

Public surface:
    from cli import run_cli          # main REPL
    from cli import console          # shared Rich console
    from cli import renderer         # rendering helpers
    from cli import ToolPanel        # live tool panel
    from cli import ToolPanelCallbackHandler  # LangChain integration
"""

from . import renderer
from .cli import run_cli
from .console import console
from .tool_panel import ToolPanel, ToolPanelCallbackHandler

__all__ = [
    "run_cli",
    "console",
    "renderer",
    "ToolPanel",
    "ToolPanelCallbackHandler",
]
