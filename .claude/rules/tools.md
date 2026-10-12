---
paths:
  - "src/khadbot/tools/**"
  - "src/khadbot/encounter/query_tools.py"
  - "src/khadbot/encounter/conversation.py"
  - "src/khadbot/encounter/ingest/fetch.py"
  - "src/khadbot/encounter/ingest/wcl_queries.py"
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

## Tool families

| Family | Where | Bound to | Returns |
|---|---|---|---|
| Coach tools | `tools/` | Coach agent, via `config/agents/coach.yaml` | Readable text for the model |
| Front tools | `encounter/conversation.py` | Encounter conversation agent, via `config/agents/encounter.yaml` | Findings, pull lists, ingest status |
| Analyst query tools | `encounter/query_tools.py` | Analyst subgraph only | `ToolResult` JSON envelope with evidence refs |

- **Analyst query tools** read only from the event store and annotations, never from an external API. The pull scope and store come from the run config (`config["configurable"]`), not from tool arguments, so the model can't query outside its request. Every returned row carries a ref.
- **Front tools** never expose raw event data. The conversation agent gets findings and status; deeper questions go through `ask_analyst`.
- **Stub tools** return `"_stub": True` in their payload. Don't treat stub output as real data when testing or evaluating behaviour.

## External API clients

- HTTP goes through `httpx` async clients used as context managers. Credentials come from `config.py`, never from tool arguments or module globals, except user-supplied tokens for private data, which are passed per call and never logged or stored.
- Clients raise typed exceptions and don't retry. Callers own retries and backoff, and honour `Retry-After` on rate limits.
- GraphQL query strings are module-level constants (`tools/warcraftlogs/_queries.py`, `encounter/ingest/wcl_queries.py`). Don't build queries inline.
- Paginated APIs are followed to the end, or truncation is reported explicitly. Never silently use the first page.
- Unit tests mock HTTP with `respx`; no tool test touches the network.
