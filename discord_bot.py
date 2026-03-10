"""
discord_bot.py

Discord bot interface for the WoW Coaching Agent.
Supports slash commands for structured input and free-text questions in DMs
or a designated channel.

Run: python discord_bot.py
Requires DISCORD_BOT_TOKEN in .env
"""

import os
import sys
import discord

# Ensure project root is on sys.path
sys.path.insert(0, os.path.dirname(__file__))
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, AIMessage

from agent.coach import build_agent_executor, ask_coach

load_dotenv()

# ---------------------------------------------------------------------------
# Bot setup
# ---------------------------------------------------------------------------

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

# One AgentExecutor shared across all interactions (stateless; history is per-user)
executor = None

# Per-user chat history keyed by Discord user ID
# In production this moves to SQLite/PostgreSQL
user_histories: dict[int, list] = {}


@bot.event
async def on_ready():
    global executor
    print(f"KhadBot logged in as {bot.user} — building agent executor...")
    executor = build_agent_executor(verbose=False)
    await bot.tree.sync()
    print("Agent ready. Slash commands synced.")


# ---------------------------------------------------------------------------
# Slash commands
# ---------------------------------------------------------------------------

@bot.tree.command(name="analyze", description="Analyze a WarcraftLogs report for a character")
@app_commands.describe(
    report="WarcraftLogs report code (e.g. aAbBcC123456)",
    character="Character name to focus on",
    boss="Optional: specific boss to filter to",
)
async def analyze(
    interaction: discord.Interaction,
    report: str,
    character: str,
    boss: str = None,
):
    await interaction.response.defer(thinking=True)
    boss_clause = f" focusing on {boss}" if boss else ""
    query = f"Analyze the WarcraftLogs report {report} for {character}{boss_clause}. What are the top things they should improve?"
    result = await _run_coach(interaction.user.id, query)
    await interaction.followup.send(result)


@bot.tree.command(name="sim", description="Run a SimulationCraft sim on your character")
@app_commands.describe(
    simc_string="Your /simc export string from in-game",
    compare="Optional: an item or talent change to compare against",
)
async def sim(
    interaction: discord.Interaction,
    simc_string: str,
    compare: str = None,
):
    await interaction.response.defer(thinking=True)
    compare_clause = f" Compare against: {compare}" if compare else ""
    query = f"Run a sim for this character.{compare_clause}\n\nSimC string:\n{simc_string}"
    result = await _run_coach(interaction.user.id, query)
    await interaction.followup.send(result)


@bot.tree.command(name="character", description="Get a quick overview of a character's progression")
@app_commands.describe(
    name="Character name",
    realm="Realm slug (e.g. area-52)",
    region="Region (us, eu, kr, tw)",
)
async def character(
    interaction: discord.Interaction,
    name: str,
    realm: str,
    region: str = "us",
):
    await interaction.response.defer(thinking=True)
    query = f"Give me an overview of {name}-{realm} ({region}). What's their progression level and M+ score?"
    result = await _run_coach(interaction.user.id, query)
    await interaction.followup.send(result)


@bot.tree.command(name="reset", description="Clear your conversation history with the coach")
async def reset(interaction: discord.Interaction):
    user_histories.pop(interaction.user.id, None)
    await interaction.response.send_message("Chat history cleared. Fresh start!", ephemeral=True)


# ---------------------------------------------------------------------------
# Free-text message handler (DMs or designated channel)
# ---------------------------------------------------------------------------

COACHING_CHANNEL_NAME = "wow-coach"  # change to match your server channel name

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    is_dm = isinstance(message.channel, discord.DMChannel)
    is_coaching_channel = (
        hasattr(message.channel, "name")
        and message.channel.name == COACHING_CHANNEL_NAME
    )

    if not (is_dm or is_coaching_channel):
        await bot.process_commands(message)
        return

    async with message.channel.typing():
        result = await _run_coach(message.author.id, message.content)

    # Discord has a 2000-char message limit; chunk if needed
    for chunk in _chunk_message(result):
        await message.reply(chunk)

    await bot.process_commands(message)


# ---------------------------------------------------------------------------
# Shared async helper
# ---------------------------------------------------------------------------

async def _run_coach(user_id: int, query: str) -> str:
    """Run the coaching agent for a user and update their history."""
    history = user_histories.get(user_id, [])

    # AgentExecutor is synchronous; run in a thread executor to avoid blocking
    import asyncio
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None, lambda: ask_coach(executor, query, history)
    )

    # Update history
    user_histories[user_id] = history + [
        HumanMessage(content=query),
        AIMessage(content=result["answer"]),
    ]

    return result["answer"]


def _chunk_message(text: str, limit: int = 1900) -> list[str]:
    """Split a long response into Discord-safe chunks."""
    if len(text) <= limit:
        return [text]
    chunks, current = [], ""
    for line in text.splitlines(keepends=True):
        if len(current) + len(line) > limit:
            chunks.append(current)
            current = ""
        current += line
    if current:
        chunks.append(current)
    return chunks


# ---------------------------------------------------------------------------
# Entry
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    token = os.getenv("DISCORD_BOT_TOKEN")
    if not token:
        raise ValueError("DISCORD_BOT_TOKEN not set in .env")
    bot.run(token)
