"""
tools/wow_tools.py
 
Central tool registry for KhadBot.
 
As each tool moves from stub to real implementation it gets its own module
and is imported here. The agent imports TOOLS from this file only — it never
needs to know which tools are real vs stubbed.
 
Status:
  get_character_raiderio  — REAL  (tools/raiderio_tool.py)
  get_warcraftlogs_report — stub
  run_simc                — stub
  search_guide_rag        — stub
"""

from langchain_core.tools import tool
from pydantic import BaseModel, Field

# Real implementations
from tools.raiderio_tool import get_character_raiderio  # noqa: F401  (re-exported)


# ---------------------------------------------------------------------------
# Stub input schemas
# ---------------------------------------------------------------------------

class WarcraftLogsInput(BaseModel):
    report_id: str = Field(description="WarcraftLogs report code, e.g. 'aAbBcC123456'")
    character_name: str = Field(description="The specific player to focus analysis on")
    boss_name: str | None = Field(
        default=None,
        description="Optional: filter to a specific boss fight, e.g. 'Queen Ansurek'"
    )


class SimCInput(BaseModel):
    simc_string: str = Field(
        description="Full SimulationCraft export string (generated via the /simc addon command)"
    )
    comparison_item: str | None = Field(
        default=None,
        description="Optional: an item string or gear change to compare against the baseline"
    )


class GuideRAGInput(BaseModel):
    spec: str = Field(description="WoW spec, e.g. 'Retribution Paladin', 'Balance Druid'")
    question: str = Field(description="The specific question to look up in the spec guide")


# ---------------------------------------------------------------------------
# Tool implementations (stubbed)
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
        "overall_parse": 42,          # gray parse — room for improvement
        "dps": 485_230,
        "ilvl_percentile": 61,        # performing below gear expectation
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


@tool("run_simc", args_schema=SimCInput)
def run_simc(simc_string: str, comparison_item: str | None = None) -> dict:
    """
    Run a SimulationCraft simulation against the player's current gear (and optionally
    compare it to an upgrade or talent change). Returns DPS estimates, stat weights,
    and upgrade recommendations. The simc_string is exported from the in-game SimC addon.
    """
    # TODO: Replace with real subprocess call to local SimC binary
    # import subprocess, tempfile, re
    # Write simc_string to a temp .simc file, run:
    #   subprocess.run(["simc", tmp_file.name, "output=/dev/stdout"], capture_output=True)
    # Parse the output for "DPS Ranking" and "Stat Weights" sections.
    baseline_dps = 512_400
    comparison_dps = 528_750 if comparison_item else None

    result = {
        "_stub": True,
        "baseline_dps": baseline_dps,
        "stat_weights": {
            "Strength": 1.00,
            "Haste": 0.87,
            "Critical Strike": 0.82,
            "Versatility": 0.74,
            "Mastery": 0.61,
        },
        "talent_note": "Current build is within 1.2% of the optimal simmed build.",
    }

    if comparison_item:
        delta = comparison_dps - baseline_dps
        result.update({
            "comparison_item": comparison_item,
            "comparison_dps": comparison_dps,
            "dps_delta": delta,
            "upgrade_recommendation": (
                f"Equipping {comparison_item} is a ~{delta:,} DPS gain "
                f"({delta / baseline_dps * 100:.1f}%). Recommended."
            ),
        })

    return result


@tool("search_guide_rag", args_schema=GuideRAGInput)
def search_guide_rag(spec: str, question: str) -> dict:
    """
    Search the Icy Veins spec guide corpus for advice relevant to the player's
    question. Returns the most relevant guide excerpts with section context.
    Use this to answer build, rotation, talent, and trinket questions grounded
    in community-accepted guidance.
    """
    # TODO: Replace with real vector store retrieval
    # from langchain_chroma import Chroma
    # from langchain_openai import OpenAIEmbeddings
    # db = Chroma(persist_directory="./chroma_db", embedding_function=OpenAIEmbeddings())
    # docs = db.similarity_search(f"{spec}: {question}", k=4)
    # return {"chunks": [{"text": d.page_content, "section": d.metadata["section"]} for d in docs]}
    return {
        "_stub": True,
        "spec": spec,
        "query": question,
        "source": "Icy Veins (stub)",
        "chunks": [
            {
                "section": "Rotation — Single Target",
                "text": (
                    "Retribution Paladin priority: maintain Judgment debuff, "
                    "spend Holy Power at 5 with Templar's Verdict (never Divine Storm "
                    "on single target). Use Wake of Ashes and Execution Sentence "
                    "inside every Avenging Wrath window."
                ),
            },
            {
                "section": "Cooldown Usage",
                "text": (
                    "Avenging Wrath should be used on cooldown unless a burn phase "
                    "is fewer than 25 seconds away. Delaying it costs significant DPS. "
                    "Always sync Execution Sentence and Wake of Ashes with Avenging Wrath."
                ),
            },
            {
                "section": "Trinkets — Current Tier",
                "text": (
                    "Best in slot trinkets for Retribution this tier: "
                    "Treacherous Transmitter (on-use, sync with Avenging Wrath) and "
                    "Ara-Kara Sacbrood (strong passive). "
                    "Avoid Entropic Skardyn's Grace on pure single-target fights."
                ),
            },
        ],
    }
