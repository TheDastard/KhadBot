"""
tools/warcraftlogs.py

ToDo - Stub
"""

from langchain_core.tools import tool
from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Input schema
# ---------------------------------------------------------------------------


class WarcraftLogsInput(BaseModel):
    report_id: str = Field(description="WarcraftLogs report code, e.g. 'aAbBcC123456'")
    character_name: str = Field(description="The specific player to focus analysis on")
    boss_name: str | None = Field(
        default=None, description="Optional: filter to a specific boss fight, e.g. 'Queen Ansurek'"
    )


# ---------------------------------------------------------------------------
# LangChain tool
# ---------------------------------------------------------------------------


@tool("get_warcraftlogs_report", args_schema=WarcraftLogsInput)
def get_warcraftlogs_report(
    report_id: str,
    character_name: str,
    boss_name: str | None = None,
) -> dict:
    """
    Pull fight-level performance data for a character from a WarcraftLogs report.
    Returns parse percentile, DPS/HPS, major cooldown usage, and top missed-cast
    opportunities. Use this to answer questions about why a player performed the
    way they did on a specific boss or across a full raid night.
    """
    # TODO: Replace with real WarcraftLogs GraphQL v2 call
    # POST https://www.warcraftlogs.com/api/v2/client
    # Uses OAuth2 client credentials; see config/.env.example for keys.
    # Key queries: reportData { report(code) { fights, rankings, playerDetails, events } }
    return {
        "_stub": True,
        "report_id": report_id,
        "character": character_name,
        "boss_filter": boss_name or "all fights",
        "overall_parse": 42,  # gray parse — room for improvement
        "dps": 485_230,
        "ilvl_percentile": 61,  # performing below gear expectation
        "cooldown_usage": {
            "Avenging_Wrath": {"casts": 3, "possible_casts": 5, "efficiency_pct": 60},
            "Wake_of_Ashes": {"casts": 11, "possible_casts": 14, "efficiency_pct": 79},
            "Execution_Sentence": {"casts": 9, "possible_casts": 14, "efficiency_pct": 64},
        },
        "top_issues": [
            "Avenging Wrath used only 3/5 possible times — large damage loss",
            "Execution Sentence frequently cast outside of Avenging Wrath window",
            "Divine Storm used instead of Templar's Verdict in single-target situations",
        ],
    }
