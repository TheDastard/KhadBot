# tools/__init__.py

from langchain_core.tools import BaseTool

from tools.rag_search import search_guide_rag
from tools.raiderio import get_character_raiderio
# from tools.wipefest import get_wipefest_insights
from tools.simc import run_simc
from tools.warcraftlogs import get_warcraftlogs_report

# The agent binds to this list. Order doesn't matter to the LLM,
# but keeping it consistent makes traces easier to read.
TOOLS: list[BaseTool] = [
    get_character_raiderio,
    get_warcraftlogs_report,
    # get_wipefest_insights,
    run_simc,
    search_guide_rag,
]
