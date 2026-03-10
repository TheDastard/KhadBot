# KhadBot — WoW Coaching Agent

An agentic AI coach for World of Warcraft players. Ask natural language questions about your character build and raid/dungeon performance and get data-grounded improvement advice.

**Current status:** Prototype with stubbed tool outputs. All four tools return hardcoded data so you can develop and test the agent's reasoning before wiring up real APIs.

---

## Setup

```bash
# 1. Clone and enter the project
cd khadbot

# 2. Create a virtual environment
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure your API key (BYOK)
cp .env.example .env
# Edit .env and add your ANTHROPIC_API_KEY (or OPENAI_API_KEY)
```

## Run the CLI

```bash
python main.py
```

Example questions to try with the stub data:

- `Why is my DPS low? Check Thralladin on area-52 and look at report aAbBcC123456`
- `What does Icy Veins say about the Ret Paladin opener?`
- `Run a sim for my character and compare the Treacherous Transmitter`

## Run the Discord Bot

```bash
# Add DISCORD_BOT_TOKEN to .env, then:
python discord_bot.py
```

Slash commands: `/analyze`, `/sim`, `/character`, `/reset`
Free-text: works in DMs or a channel named `#wow-coach`

---

## Project Structure

```
khadbot/
├── tools/
│   └── wow_tools.py       # Tool stubs — replace bodies to go live
├── agent/
│   └── coach.py           # Tool-calling agent setup and BYOK LLM factory
├── main.py                # CLI entrypoint
├── discord_bot.py         # Discord bot
├── requirements.txt
└── .env.example
```

## Replacing Stubs with Real Implementations

Each tool has a `# TODO` comment pointing to the real API call. The stub return shape matches what the real API will return, so the agent's prompting and reasoning won't change when you swap them in.

| Tool | What to build |
|---|---|
| `get_character_raiderio` | `GET raider.io/api/v1/characters/profile` |
| `get_warcraftlogs_report` | WarcraftLogs GraphQL v2 with OAuth2 client credentials |
| `run_simc` | `subprocess.run(["simc", ...])` against a local SimC binary |
| `search_guide_rag` | Chroma vector store similarity search over scraped Icy Veins pages |

## Switching LLM Providers

Set in `.env`:

```
# Anthropic (default)
LLM_PROVIDER=anthropic
LLM_MODEL=claude-sonnet-4-20250514
ANTHROPIC_API_KEY=sk-ant-...

# OpenAI
LLM_PROVIDER=openai
LLM_MODEL=gpt-4o
OPENAI_API_KEY=sk-...
```
