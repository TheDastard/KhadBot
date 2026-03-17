"""
tools/simc.py

ToDo - Stub
"""

from langchain_core.tools import tool
from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Input schema
# ---------------------------------------------------------------------------


class SimCInput(BaseModel):
    simc_string: str = Field(description="Full SimulationCraft export string (generated via the /simc addon command)")
    comparison_item: str | None = Field(
        default=None,
        description="Optional: an item string or gear change to compare against the baseline",
    )


# ---------------------------------------------------------------------------
# LangChain tool
# ---------------------------------------------------------------------------


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
        result.update(
            {
                "comparison_item": comparison_item,
                "comparison_dps": comparison_dps,
                "dps_delta": delta,
                "upgrade_recommendation": (
                    f"Equipping {comparison_item} is a ~{delta:,} DPS gain "
                    f"({delta / baseline_dps * 100:.1f}%). Recommended."
                ),
            }
        )

    return result
