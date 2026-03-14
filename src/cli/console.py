"""
console.py — Shared Rich console instance and WoW-themed styling.

Import `console` from here everywhere in the CLI layer. Never instantiate
a second Console; multiple Console instances fight over the terminal.
"""

from rich.console import Console
from rich.theme import Theme

# ---------------------------------------------------------------------------
# WoW-flavoured colour palette
# Named after in-game item quality tiers so the rest of the codebase can
# use semantic names rather than raw colour strings.
# ---------------------------------------------------------------------------
WOW_THEME = Theme({
    # Item quality / parse tiers
    "quality.poor":       "white",
    "quality.common":     "bright_white",
    "quality.uncommon":   "green",
    "quality.rare":       "bright_blue",
    "quality.epic":       "bright_magenta",
    "quality.legendary":  "orange1",
    "quality.artifact":   "#e6cc80",   # pale gold

    # UI chrome
    "ui.header":          "bold bright_cyan",
    "ui.subheader":       "bold cyan",
    "ui.muted":           "dim white",
    "ui.prompt":          "bold bright_yellow",
    "ui.success":         "bold green",
    "ui.warning":         "bold yellow",
    "ui.error":           "bold red",

    # Agent / tool states
    "tool.pending":       "dim white",
    "tool.running":       "bright_cyan",
    "tool.done":          "green",
    "tool.failed":        "red",

    # Data labels
    "label.key":          "dim cyan",
    "label.value":        "bright_white",
})

# Single shared console — import this everywhere
console = Console(theme=WOW_THEME, highlight=False)
