---
paths:
  - "src/khadbot/agent/**"
  - "config/agents/**"
  - "config/personas/**"
  - "config/templates/**"
---

# Agent, persona and prompt config

- Each mode's agent is declared in `config/agents/<name>.yaml` (`coach.yaml`, `encounter.yaml`): `agent.name`, `agent.version`, `agent.base_prompt`, `tools` (names of Python `@tool` functions) and `personas` (IDs resolved to `config/personas/<id>.yaml`).
- `load_agent_config()` validates the YAML and checks declared tool names against the Python tools the caller passes in. A declared tool with no implementation is a `ConfigurationError` at startup. Keep it that way: fail at load, not at first request.
- A tool must be both implemented and listed in the agent's YAML to be available to that agent. Encounter analyst tools are never listed in any agent YAML; only the analyst subgraph binds them.
- Persona files need `id` (a slug matching the filename), `display_name`, `intro_message` and `voice_prompt`. Persona voice and guardrails are injected only through `config/templates/prompt.jinja2`; never hard-code them in Python.
- Personas behave the same in both modes because both render their system prompt through `PromptAssembler`.
- The rendered prompt is built once per agent instance. Changing persona means rebuilding the agent.
- Bump `agent.version` when you change a base prompt.
