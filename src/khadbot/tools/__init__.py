"""
tools/__init__.py — ToDo

Public surface:
    from tools import find_character_reports
    from tools import get_character_raiderio
    from tools import get_encounter_analysis
    from tools import get_warcraftlogs_report
    from tools import get_wipefest_insights
    from tools import run_simc
    from tools import search_guide_rag
"""

from langchain_core.tools import BaseTool

from khadbot.tools.rag_search import search_guide_rag
from khadbot.tools.raiderio import get_character_raiderio
from khadbot.tools.simc import run_simc
from khadbot.tools.warcraftlogs import (
    find_character_reports,
    get_encounter_analysis,
    get_warcraftlogs_report,
)
from khadbot.tools.wipefest import get_wipefest_insights

# The agent binds to this list. Order doesn't matter to the LLM,
# but keeping it consistent makes traces easier to read.
TOOLS: list[BaseTool] = [
    find_character_reports,
    get_character_raiderio,
    get_warcraftlogs_report,
    get_encounter_analysis,
    get_wipefest_insights,
    run_simc,
    search_guide_rag,
]
