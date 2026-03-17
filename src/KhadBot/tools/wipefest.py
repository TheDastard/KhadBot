"""
tools/wipefest.py

ToDo - Stub
"""

from langchain_core.tools import tool
from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Input schema
# ---------------------------------------------------------------------------


class WipefestInput(BaseModel):
    report_id: str = Field(description="WarcraftLogs report code, e.g. 'aAbBcC123456'")
    fight_id: int = Field(description="Numeric fight ID within the report. Use 'last' fight or a specific boss ID.")


# ---------------------------------------------------------------------------
# LangChain tool
# ---------------------------------------------------------------------------


@tool("get_wipefest_insights", args_schema=WipefestInput)
def get_wipefest_insights(report_id: str, fight_id: int) -> dict:
    """
    Fetch mechanical failure analysis for a specific boss fight from Wipefest.
    Returns missed interrupts, avoidable damage taken, failed soaks, and other
    mechanic-specific failures per player. Use this alongside WarcraftLogs data
    to answer 'why did my parse suffer beyond just rotation?' — Wipefest explains
    what went wrong mechanically, not just numerically.
    """
    # TODO: Replace with real Wipefest call.
    # Two options documented in the project overview:
    #   Option A — Direct HTTP: call the Wipefest web API from Python
    #   Option B — Node.js sidecar: thin Express service wrapping wipefest-core npm SDK,
    #              exposing a REST endpoint consumed here via httpx
    # Option B is preferred for production (self-hostable, no external API dependency).
    return {
        "_stub": True,
        "report_id": report_id,
        "fight_id": fight_id,
        "boss": "Queen Ansurek",
        "deaths": [
            {
                "player": "Thralladin",
                "cause": "Stood in Silken Tomb during Reactive Web — avoidable",
                "timestamp": "4:12",
            },
        ],
        "missed_interrupts": [
            {
                "player": "Thralladin",
                "ability": "Venom Nova",
                "count": 2,
                "impact": "High — each miss applied a raid-wide DoT",
            },
        ],
        "avoidable_damage": [
            {
                "player": "Thralladin",
                "source": "Silken Tomb splash",
                "total_damage": 840_000,
                "hits": 3,
                "note": "Reposition earlier when Silken Tomb is targeted on a nearby player",
            },
        ],
        "failed_soaks": [],
        "summary": (
            "Thralladin missed 2 interrupts on Venom Nova and took avoidable splash "
            "damage from Silken Tomb 3 times. These account for roughly 12% of total "
            "damage taken for the fight. Prioritize interrupt assignment and positioning "
            "awareness on this encounter."
        ),
    }
