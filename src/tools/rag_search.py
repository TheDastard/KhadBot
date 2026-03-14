"""
tools/rag_search.py

ToDo - Stub
"""

from langchain_core.tools import tool
from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Input schema
# ---------------------------------------------------------------------------


class GuideRAGInput(BaseModel):
    spec: str = Field(description="WoW spec, e.g. 'Retribution Paladin', 'Balance Druid'")
    question: str = Field(description="The specific question to look up in the spec guide")


# ---------------------------------------------------------------------------
# LangChain tool
# ---------------------------------------------------------------------------


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
