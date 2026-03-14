"""
agent/coach.py

Core coaching agent for KhadBot.

Uses langchain.agents.create_agent — the current recommended LangChain entry
point, built on LangGraph under the hood. This replaces both the legacy
AgentExecutor pattern and the hand-rolled LCEL loop.

The Pydantic v1 UserWarning on Python 3.14+ is an open bug in langchain_core
itself (not fixable by changing agent constructors). It's a warning only —
the agent runs correctly. Track: https://github.com/langchain-ai/langchain/issues
"""

from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain_core.messages import HumanMessage

from tools import TOOLS

load_dotenv()

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are KhadBot, a World of Warcraft performance coach with deep knowledge \
of all specs, the current raid tier, and Mythic+ meta. Your job is to give players \
specific, actionable advice grounded in real data — not generic tips.

You have four tools:
- get_character_raiderio: fetch a player's progression level and M+ score
- get_warcraftlogs_report: pull parse data, cooldown usage, and top inefficiencies from a log
- run_simc: simulate a player's gear or compare an upgrade
- search_guide_rag: look up Icy Veins guide content for rotations, talents, and trinkets

Coaching principles:
1. Always ground advice in data. Don't recommend something without citing the log or guide.
2. Prioritize the highest-impact issues first. Don't overwhelm with a wall of fixes.
3. Be direct and specific. Bad: "use cooldowns more". Good: "Avenging Wrath was used 3/5 \
   possible times — casting it on cooldown is worth roughly 8% DPS."
4. Acknowledge the player's progression context from Raider.IO — advice for a Mythic prog \
   player differs from a Heroic casual.
5. If you don't have enough information (missing log, unknown spec), ask one focused \
   follow-up question rather than guessing.

When tools return data marked "_stub": true, treat the values as real for coaching purposes \
— the stub layer will be replaced with live data without changing your reasoning.
"""

# ---------------------------------------------------------------------------
# Agent factory
# ---------------------------------------------------------------------------


def build_agent_executor(verbose: bool = True):
    """
    Build and return a KhadBot agent using langchain.agents.create_agent,
    the current recommended LangChain agent entry point.
    """
    from llm_factory import get_llm

    llm = get_llm()

    agent = create_agent(
        model=llm,
        tools=TOOLS,
        system_prompt=SYSTEM_PROMPT,
    )

    return agent


# ---------------------------------------------------------------------------
# Single-turn helper (used by CLI and Discord bot)
# ---------------------------------------------------------------------------


def ask_coach(
    agent,
    user_message: str,
    chat_history: list | None = None,
) -> dict:
    """
    Run one turn of the coaching conversation.

    Args:
        agent:        Agent built by build_agent_executor()
        user_message: The player's question
        chat_history: List of prior (HumanMessage, AIMessage) pairs

    Returns:
        dict with keys: "answer" (str), "steps" (list of intermediate tool calls)
    """
    messages = list(chat_history or [])
    messages.append(HumanMessage(content=user_message))

    result = agent.invoke({"messages": messages})

    # create_agent returns a dict with a "messages" list; the last message is the answer
    output_messages = result.get("messages", [])
    answer = output_messages[-1].content if output_messages else ""

    # Collect any tool call steps from the message history
    steps = [
        (m.name, m.content) for m in output_messages if hasattr(m, "type") and m.type == "tool"
    ]

    return {"answer": answer, "steps": steps}
