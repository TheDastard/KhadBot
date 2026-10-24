---
paths:
  - "src/khadbot/agent/tools/**"
  - "src/khadbot/encounter/analyst/query_tools.py"
  - "src/khadbot/encounter/conversation.py"
  - "src/khadbot/encounter/ingest/fetch.py"
  - "src/khadbot/encounter/ingest/wcl_queries.py"
  - "src/khadbot/wcl/**"
---

# LangChain tools

## All tools

- Tools are `@tool` functions with typed arguments. The docstring is the model's only description of the tool: say when to use it, when not to, and what each argument means, with an example value.
- Docstring and argument changes are prompt changes. They change model behaviour, and for encounter tools they change the prompt version and invalidate cached findings.
- A tool never raises to the agent. Every failure path (bad input, missing data, auth failure, rate limit, API error) returns a result the model can read and act on, naming what went wrong and what to try instead.
- Keep output bounded. Cap rows and characters, and say when output was truncated and how to narrow the query.
- Tools stay thin: parse arguments, call a plain function, format the result. Put the logic in that function so it can be tested without LangChain.
- A tool is available to an agent only when it is implemented and wired to that agent: listed in the agent's YAML for agents built from config, or bound in code for the analyst subgraph.
- Tool names are stable identifiers referenced from YAML, tests and traces. Rename one only deliberately, everywhere at once.
- No agent tool calls Warcraft Logs, SimulationCraft or Raider.IO. Offline pipelines fetch and cache that data; tools query the cache locally.

## Tool families

| Family | Where | Bound to | Returns |
|---|---|---|---|
| Agent tools | `agent/tools/` (empty for now) | Coach agent, via `config/agents/coach.yaml` | Readable text for the model, from locally cached data |
| Front tools | `encounter/conversation.py` | Encounter conversation agent, via `config/agents/encounter.yaml` | Findings, pull lists, ingest status |
| Analyst query tools | `encounter/analyst/query_tools.py` | Analyst subgraph only | `ToolResult` JSON envelope with evidence refs |

- **Analyst query tools** read only from the event store and annotations, never from an external API. The pull scope and store come from the run config (`config["configurable"]`), not from tool arguments, so the model can't query outside its request. Every returned row carries a ref.
- **Front tools** never expose raw event data. The conversation agent gets findings and status; deeper questions go through `ask_analyst`.

## External API clients

These apply to the clients the offline pipelines use (`wcl/`, `encounter/ingest/`), not to agent tools.

- HTTP goes through `httpx` async clients used as context managers. Credentials come from `config.py`, never from tool arguments or module globals, except user-supplied tokens for private data, which are passed per call and never logged or stored.
- Clients raise typed exceptions and don't retry. Callers own retries and backoff, and honour `Retry-After` on rate limits.
- GraphQL query strings are module-level constants (`encounter/ingest/wcl_queries.py`). Don't build queries inline.
- Paginated APIs are followed to the end, or truncation is reported explicitly. Never silently use the first page.
- Unit tests mock HTTP with `respx`; no unit test touches the network.
