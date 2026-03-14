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
    fight_id: int = Field(description="Numeric fight ID within the WarcractLogs report")


# ---------------------------------------------------------------------------
# LangChain tool
# ---------------------------------------------------------------------------


@tool("get_wipefest_insights", args_schema=WipefestInput)
def get_wipefest_insights(report_id: str, fight_id: int) -> dict:
    """
    ToDo
    """
    raise NotImplementedError("get_wipefest_insights is not yet implemented. ")
