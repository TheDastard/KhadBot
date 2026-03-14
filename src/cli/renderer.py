"""
renderer.py — Rich rendering helpers for the WoW Coaching Agent CLI.

All visual output lives here. The agent and tool layers stay plain Python;
they return structured data and this module decides how to display it.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from rich.columns import Columns
from rich.markdown import Markdown
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from .console import console

# ---------------------------------------------------------------------------
# Parse percentile → WoW colour (mirrors WarcraftLogs colour scheme)
# ---------------------------------------------------------------------------

ParseTier = Literal["poor", "common", "uncommon", "rare", "epic", "legendary"]


def parse_tier(percentile: int) -> ParseTier:
    if percentile >= 95:
        return "legendary"
    if percentile >= 75:
        return "epic"
    if percentile >= 50:
        return "rare"
    if percentile >= 25:
        return "uncommon"
    if percentile >= 5:
        return "common"
    return "poor"


def render_parse(percentile: int, spec: str = "", boss: str = "") -> Text:
    """Return a styled Text object for a parse percentile."""
    tier = parse_tier(percentile)
    style = f"quality.{tier}"
    label = f"{percentile}th"
    if spec:
        label = f"{spec} — {label}"
    if boss:
        label = f"{boss}  {label}"
    return Text(label, style=style)


# ---------------------------------------------------------------------------
# Welcome banner
# ---------------------------------------------------------------------------

def render_banner() -> None:
    banner_text = Text(justify="center")
    banner_text.append("⚔  ", style="quality.legendary")
    banner_text.append("KhadBot- Agentic WoW Assitant", style="ui.header")
    banner_text.append("  ⚔", style="quality.legendary")

    subtitle = Text("Ask anything about your character, logs, or gear.", justify="center", style="ui.muted")

    console.print()
    console.print(Panel(
        f"{banner_text}\n{subtitle}",
        border_style="bright_cyan",
        padding=(1, 4),
    ))
    console.print()


# ---------------------------------------------------------------------------
# Conversation rendering
# ---------------------------------------------------------------------------

def render_user_message(message: str) -> None:
    """Print the user's message in the conversation transcript."""
    ts = Text(datetime.now().strftime("%H:%M"), style="ui.muted")
    label = Text(" You  ", style="bold bright_yellow on grey19")
    header = Text.assemble(ts, "  ", label)
    console.print(header)
    console.print(f"  {message}", style="bright_white")
    console.print()


def render_agent_response(response: str) -> None:
    """
    Render the agent's coaching response.
    Uses Rich Markdown so headers, bold, bullet lists, and code blocks
    in the LLM output render properly rather than as raw markup.
    """
    console.print(Rule(style="dim cyan"))
    console.print(
        Panel(
            Markdown(response),
            title="[ui.subheader]KhadBot - Personal Coach[/ui.subheader]",
            border_style="cyan",
            padding=(1, 2),
        )
    )
    console.print()


# ---------------------------------------------------------------------------
# Character context card
# ---------------------------------------------------------------------------

def render_character_card(
    name: str,
    realm: str,
    region: str,
    spec: str,
    mythic_plus_score: float | None = None,
    raid_progress: str | None = None,
) -> None:
    """Compact character summary card shown when a session is oriented to a character."""
    table = Table.grid(padding=(0, 2))
    table.add_column(style="label.key", justify="right")
    table.add_column(style="label.value")

    table.add_row("Character", f"{name}-{realm} [{region.upper()}]")
    table.add_row("Spec", spec)
    if mythic_plus_score is not None:
        score_style = _mplus_score_style(mythic_plus_score)
        table.add_row("M+ Score", Text(str(mythic_plus_score), style=score_style))
    if raid_progress:
        table.add_row("Raid", raid_progress)

    console.print(Panel(table, title="[ui.subheader]Character[/ui.subheader]", border_style="bright_blue", padding=(0, 1)))
    console.print()


def _mplus_score_style(score: float) -> str:
    if score >= 3000:
        return "quality.legendary"
    if score >= 2500:
        return "quality.epic"
    if score >= 2000:
        return "quality.rare"
    if score >= 1500:
        return "quality.uncommon"
    return "quality.common"


# ---------------------------------------------------------------------------
# SimC results table
# ---------------------------------------------------------------------------

def render_simc_results(results: list[dict]) -> None:
    """
    Render SimulationCraft comparison output as a table.

    Each dict in `results` should have:
        label: str          — e.g. "Current Gear", "With Trinket X"
        mean_dps: float
        min_dps: float
        max_dps: float
        delta: float | None — DPS difference vs. the first (baseline) row
    """
    table = Table(
        title="SimulationCraft Results",
        border_style="bright_blue",
        header_style="ui.subheader",
        show_lines=False,
    )
    table.add_column("Profile", style="label.value", no_wrap=True)
    table.add_column("Mean DPS", justify="right")
    table.add_column("Range", justify="right", style="ui.muted")
    table.add_column("Δ vs Baseline", justify="right")

    for i, row in enumerate(results):
        delta_text = Text("—", style="ui.muted")
        if i > 0 and row.get("delta") is not None:
            delta = row["delta"]
            sign = "+" if delta >= 0 else ""
            delta_text = Text(
                f"{sign}{delta:,.0f}",
                style="quality.uncommon" if delta >= 0 else "quality.poor",
            )

        table.add_row(
            row["label"],
            f"{row['mean_dps']:,.0f}",
            f"{row['min_dps']:,.0f} – {row['max_dps']:,.0f}",
            delta_text,
        )

    console.print()
    console.print(table)
    console.print()


# ---------------------------------------------------------------------------
# Parse breakdown table
# ---------------------------------------------------------------------------

def render_parse_table(parses: list[dict]) -> None:
    """
    Render a WarcraftLogs parse breakdown.

    Each dict: { boss, spec, percentile, dps, ilvl }
    """
    table = Table(
        title="WarcraftLogs Parses",
        border_style="bright_blue",
        header_style="ui.subheader",
        show_lines=False,
    )
    table.add_column("Boss", style="label.value", no_wrap=True)
    table.add_column("Spec")
    table.add_column("Parse", justify="right")
    table.add_column("DPS", justify="right", style="ui.muted")
    table.add_column("iLvl", justify="right", style="ui.muted")

    for row in parses:
        pct = row["percentile"]
        tier = parse_tier(pct)
        table.add_row(
            row["boss"],
            row["spec"],
            Text(f"{pct}th", style=f"quality.{tier}"),
            f"{row['dps']:,.0f}",
            str(row["ilvl"]),
        )

    console.print()
    console.print(table)
    console.print()


# ---------------------------------------------------------------------------
# Error / warning helpers
# ---------------------------------------------------------------------------

def render_error(message: str, title: str = "Error") -> None:
    console.print(Panel(message, title=f"[ui.error]{title}[/ui.error]", border_style="red"))
    console.print()


def render_warning(message: str) -> None:
    console.print(f"[ui.warning]⚠  {message}[/ui.warning]")
    console.print()
