"""
agent/prompt_assembler.py

Renders the system prompt from a Jinja2 template, an AgentConfig, and an
optional active persona.

Responsibilities:
  - Load and cache the prompt template from config/prompt_template.jinja2
  - Render it with the agent's base_prompt and (optionally) a persona
  - Nothing else — no tool wrangling, no LLM concerns

The persona guardrail is baked into the template itself as a conditional
block, so this module stays simple: it just passes context to the renderer.

Usage:
    assembler = PromptAssembler()  # loads template from default path
    prompt = assembler.render(config, persona=None)   # base prompt only
    prompt = assembler.render(config, persona=cfg)    # with persona + guardrail
"""

from __future__ import annotations

from functools import cached_property
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from khadbot.agent.agent_config import AgentConfig, PersonaConfig

_DEFAULT_TEMPLATE_PATH = Path(__file__).parent.parent.parent / "config" / "templates" / "prompt.jinja2"


class PromptAssembler:
    """
    Renders the system prompt template for a given AgentConfig and persona.

    Args:
        template_path: Path to the Jinja2 template file. Defaults to
                       config/prompt_template.jinja2. Pass an explicit path
                       in tests to use fixture templates.
    """

    def __init__(self, template_path: Path | str | None = None) -> None:
        self._template_path = Path(template_path) if template_path else _DEFAULT_TEMPLATE_PATH

    @cached_property
    def _template(self):
        """Load and cache the Jinja2 template. Raises on first access if missing."""
        if not self._template_path.exists():
            raise FileNotFoundError(f"Prompt template not found at: {self._template_path}")
        env = Environment(
            loader=FileSystemLoader(str(self._template_path.parent)),
            undefined=StrictUndefined,  # raise on missing variables — no silent blanks
            keep_trailing_newline=True,
        )
        return env.get_template(self._template_path.name)

    def render(
        self,
        config: AgentConfig,
        persona: PersonaConfig | None = None,
    ) -> str:
        """
        Render the system prompt for the given config and persona.

        Args:
            config:  The AgentConfig to render for.
            persona: Active PersonaConfig, or None for no-persona mode.
                     When provided, the template injects the guardrail and
                     voice_prompt blocks automatically.

        Returns:
            The fully rendered system prompt string, ready to pass to
            create_agent().
        """
        return self._template.render(
            base_prompt=config.base_prompt,
            persona=persona,
        )


# ---------------------------------------------------------------------------
# Module-level default assembler
# ---------------------------------------------------------------------------

# Instantiated once; PromptAssembler.render() is called per agent build.
# Tests that need a custom template should instantiate PromptAssembler(path)
# directly rather than using this singleton.
_default_assembler: PromptAssembler | None = None


def get_assembler() -> PromptAssembler:
    """Return the module-level default PromptAssembler, creating it on first call."""
    global _default_assembler
    if _default_assembler is None:
        _default_assembler = PromptAssembler()
    return _default_assembler
