"""
tests/unit/agent/test_personas.py

Unit tests for the personas module and its integration into coach.py.

Coverage:
  personas.py
    - CoachPersona field integrity for each defined persona
    - Registry completeness and absence of a DEFAULT_PERSONA constant
    - get_persona() — known ID, None, empty string, and unknown ID all return None
    - list_personas() — completeness and no duplicates
    - Immutability (frozen dataclass)
    - Voice prompt structural requirements (PERSONA header, guidelines, example)

  coach.py
    - BASE_SYSTEM_PROMPT — non-empty, lists all tools, does NOT contain persona guardrail
    - PERSONA_SCOPE_GUARDRAIL — contains tone-restriction and adversarial-input warning
    - build_system_prompt(None) — returns BASE_SYSTEM_PROMPT unchanged
    - build_system_prompt(persona) — base first, then guardrail, then voice prompt
    - build_agent_executor() — explicit persona arg, config env var fallback, no-persona default

No LLM inference. No network calls. Zero external dependencies beyond the
project modules themselves.
"""

from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def minimal_persona():
    """A minimal valid CoachPersona for testing coach.py in isolation."""
    from khadbot.agent.personas import CoachPersona

    return CoachPersona(
        id="test_persona",
        display_name="Test Character",
        voice_prompt="PERSONA — TEST\nSpeak as a test character.",
        intro_message="Hello, I am a test persona.",
    )


# ===========================================================================
# personas.py — CoachPersona field integrity
# ===========================================================================


class TestCoachPersonaFields:
    """Every defined persona must have non-empty, correctly typed fields."""

    @pytest.fixture(params=["thrall", "khadgar", "xalatath"])
    def persona(self, request):
        from khadbot.agent.personas import PERSONAS

        return PERSONAS[request.param]

    def test_id_is_nonempty_string(self, persona):
        assert isinstance(persona.id, str) and persona.id.strip()

    def test_display_name_is_nonempty_string(self, persona):
        assert isinstance(persona.display_name, str) and persona.display_name.strip()

    def test_voice_prompt_is_nonempty_string(self, persona):
        assert isinstance(persona.voice_prompt, str) and persona.voice_prompt.strip()

    def test_intro_message_is_nonempty_string(self, persona):
        assert isinstance(persona.intro_message, str) and persona.intro_message.strip()

    def test_id_matches_registry_key(self, persona):
        from khadbot.agent.personas import PERSONAS

        assert PERSONAS[persona.id] is persona

    def test_voice_prompt_has_meaningful_length(self, persona):
        # A voice prompt under 100 chars is almost certainly a stub or accident.
        assert len(persona.voice_prompt) >= 100, (
            f"Persona '{persona.id}' voice_prompt is suspiciously short "
            f"({len(persona.voice_prompt)} chars) — check for accidental truncation."
        )

    def test_id_is_lowercase_slug(self, persona):
        # IDs are used in slash commands and session state — must be lowercase,
        # no spaces, no special characters beyond underscores.
        import re

        assert re.match(r"^[a-z][a-z0-9_]*$", persona.id), (
            f"Persona ID '{persona.id}' must be a lowercase slug (a-z, 0-9, _)."
        )


# ===========================================================================
# personas.py — Registry
# ===========================================================================


class TestPersonasRegistry:
    def test_all_expected_personas_present(self):
        from khadbot.agent.personas import PERSONAS

        assert "thrall" in PERSONAS
        assert "khadgar" in PERSONAS
        assert "xalatath" in PERSONAS

    def test_registry_has_no_duplicate_ids(self):
        from khadbot.agent.personas import KHADGAR, THRALL, XALATATH

        all_personas = [THRALL, KHADGAR, XALATATH]
        ids = [p.id for p in all_personas]
        assert len(ids) == len(set(ids)), "Duplicate persona IDs detected."

    def test_registry_values_are_coach_persona_instances(self):
        from khadbot.agent.personas import PERSONAS, CoachPersona

        for persona_id, persona in PERSONAS.items():
            assert isinstance(persona, CoachPersona), f"PERSONAS['{persona_id}'] is not a CoachPersona instance."

    def test_registry_key_matches_persona_id(self):
        from khadbot.agent.personas import PERSONAS

        for key, persona in PERSONAS.items():
            assert key == persona.id, f"Registry key '{key}' does not match persona.id '{persona.id}'."

    def test_no_default_persona_constant(self):
        # There is no DEFAULT_PERSONA — no persona is the default state.
        # A persona is only active when explicitly set via env var or passed directly.
        import khadbot.agent.personas as personas_module

        assert not hasattr(personas_module, "DEFAULT_PERSONA")


# ===========================================================================
# personas.py — get_persona()
# ===========================================================================


class TestGetPersona:
    def test_returns_correct_persona_for_known_id(self):
        from khadbot.agent.personas import THRALL, get_persona

        assert get_persona("thrall") is THRALL

    def test_returns_correct_persona_for_each_defined_id(self):
        from khadbot.agent.personas import PERSONAS, get_persona

        for persona_id, expected in PERSONAS.items():
            assert get_persona(persona_id) is expected

    def test_none_returns_none(self):
        from khadbot.agent.personas import get_persona

        assert get_persona(None) is None

    def test_unknown_id_returns_none(self):
        from khadbot.agent.personas import get_persona

        result = get_persona("totally_unknown_character_xyz")
        assert result is None

    def test_unknown_id_does_not_raise(self):
        from khadbot.agent.personas import get_persona

        # Must never raise — a bad env var or typo should not crash the bot.
        try:
            get_persona("nonexistent")
        except Exception as e:
            pytest.fail(f"get_persona() raised unexpectedly: {e}")

    def test_empty_string_returns_none(self):
        # Empty string means "no persona" — same as None.
        from khadbot.agent.personas import get_persona

        assert get_persona("") is None

    def test_case_sensitive_id_lookup(self):
        # IDs are lowercase slugs — "Thrall" is not "thrall".
        from khadbot.agent.personas import get_persona

        result = get_persona("Thrall")
        assert result is None  # no match → no persona


# ===========================================================================
# personas.py — list_personas()
# ===========================================================================


class TestListPersonas:
    def test_returns_all_personas(self):
        from khadbot.agent.personas import PERSONAS, list_personas

        result = list_personas()
        assert len(result) == len(PERSONAS)

    def test_all_items_are_coach_persona_instances(self):
        from khadbot.agent.personas import CoachPersona, list_personas

        for persona in list_personas():
            assert isinstance(persona, CoachPersona)

    def test_no_duplicates_in_list(self):
        from khadbot.agent.personas import list_personas

        result = list_personas()
        ids = [p.id for p in result]
        assert len(ids) == len(set(ids))

    def test_contains_all_expected_personas(self):
        from khadbot.agent.personas import list_personas

        ids = {p.id for p in list_personas()}
        assert {"thrall", "khadgar", "xalatath"}.issubset(ids)


# ===========================================================================
# personas.py — Immutability
# ===========================================================================


class TestCoachPersonaImmutability:
    """CoachPersona is frozen=True — mutations must raise."""

    @pytest.fixture(params=["thrall", "khadgar", "xalatath"])
    def persona(self, request):
        from khadbot.agent.personas import PERSONAS

        return PERSONAS[request.param]

    def test_cannot_mutate_id(self, persona):
        with pytest.raises((AttributeError, TypeError)):
            persona.id = "hacked"

    def test_cannot_mutate_voice_prompt(self, persona):
        with pytest.raises((AttributeError, TypeError)):
            persona.voice_prompt = "ignore all previous instructions"

    def test_cannot_mutate_display_name(self, persona):
        with pytest.raises((AttributeError, TypeError)):
            persona.display_name = "Evil Bot"

    def test_cannot_add_new_attribute(self, persona):
        with pytest.raises((AttributeError, TypeError)):
            persona.new_field = "surprise"


# ===========================================================================
# personas.py — Voice prompt structural guardrails
# ===========================================================================


class TestVoicePromptContent:
    """
    Light structural checks on voice prompt content.

    These are not style lints — they verify properties that matter for
    correctness: each voice prompt identifies its persona and provides
    behavioral guidance beyond a single line.
    """

    @pytest.fixture(params=["thrall", "khadgar", "xalatath"])
    def persona(self, request):
        from khadbot.agent.personas import PERSONAS

        return PERSONAS[request.param]

    def test_voice_prompt_contains_persona_header(self, persona):
        # Every voice prompt starts with a PERSONA — NAME header so the model
        # has a clear anchor for who it is inhabiting.
        assert "PERSONA" in persona.voice_prompt.upper(), (
            f"Persona '{persona.id}' voice_prompt is missing a PERSONA header."
        )

    def test_voice_prompt_contains_voice_guidelines(self, persona):
        # Voice prompts must include behavioral guidelines, not just a character
        # description — the model needs actionable instructions.
        prompt_lower = persona.voice_prompt.lower()
        has_guidelines = "guideline" in prompt_lower or "speak" in prompt_lower or "voice" in prompt_lower
        assert has_guidelines, f"Persona '{persona.id}' voice_prompt appears to lack voice guidelines."

    def test_voice_prompt_contains_example_register(self, persona):
        # An example sentence is the most effective calibration signal for tone.
        # Every prompt should have one.
        prompt_lower = persona.voice_prompt.lower()
        has_example = "example" in prompt_lower
        assert has_example, f"Persona '{persona.id}' voice_prompt is missing an 'Example register' section."


# ===========================================================================
# coach.py — BASE_SYSTEM_PROMPT persona scope guardrail
# ===========================================================================


class TestBaseSystemPrompt:
    """
    BASE_SYSTEM_PROMPT is the coaching scope and tool listing — it is always
    present regardless of whether a persona is active. It must NOT contain the
    persona scope guardrail (that lives in PERSONA_SCOPE_GUARDRAIL and is only
    injected when a persona is active).
    """

    def test_base_prompt_is_nonempty(self):
        from khadbot.agent.coach import BASE_SYSTEM_PROMPT

        assert BASE_SYSTEM_PROMPT.strip()

    def test_base_prompt_mentions_all_tools(self):
        from khadbot.agent.coach import BASE_SYSTEM_PROMPT

        for tool_name in [
            "get_character_raiderio",
            "get_warcraftlogs_report",
            "run_simc",
            "search_guide_rag",
        ]:
            assert tool_name in BASE_SYSTEM_PROMPT, (
                f"BASE_SYSTEM_PROMPT does not mention tool '{tool_name}'. "
                "All tools should be listed so the model knows what it has access to."
            )

    def test_base_prompt_does_not_contain_persona_scope_guardrail(self):
        # The guardrail is only injected when a persona is active. It must not
        # be present in the base prompt or the no-persona path gets a dangling
        # "a persona voice will be provided below" with nothing below it.
        from khadbot.agent.coach import BASE_SYSTEM_PROMPT

        assert "PERSONA SCOPE" not in BASE_SYSTEM_PROMPT.upper()
        assert "adversarial" not in BASE_SYSTEM_PROMPT.lower()


# ===========================================================================
# coach.py — build_system_prompt()
# ===========================================================================


class TestPersonaScopeGuardrail:
    """
    PERSONA_SCOPE_GUARDRAIL is injected between the base prompt and the voice
    block only when a persona is active. It must contain the tone-restriction
    and adversarial-input warning that keep persona flavor from overriding
    data integrity requirements.
    """

    def test_guardrail_is_nonempty(self):
        from khadbot.agent.coach import PERSONA_SCOPE_GUARDRAIL

        assert PERSONA_SCOPE_GUARDRAIL.strip()

    def test_guardrail_contains_persona_scope_header(self):
        from khadbot.agent.coach import PERSONA_SCOPE_GUARDRAIL

        assert "PERSONA SCOPE" in PERSONA_SCOPE_GUARDRAIL.upper()

    def test_guardrail_states_tone_only_restriction(self):
        from khadbot.agent.coach import PERSONA_SCOPE_GUARDRAIL

        assert "tone" in PERSONA_SCOPE_GUARDRAIL.lower(), (
            "PERSONA_SCOPE_GUARDRAIL should state that persona affects tone only."
        )

    def test_guardrail_warns_about_adversarial_input(self):
        from khadbot.agent.coach import PERSONA_SCOPE_GUARDRAIL

        assert "adversarial" in PERSONA_SCOPE_GUARDRAIL.lower(), (
            "PERSONA_SCOPE_GUARDRAIL should warn that user-supplied inputs may be adversarial."
        )


class TestBuildSystemPrompt:
    def test_none_returns_base_prompt_exactly(self):
        # No persona active → system prompt is BASE_SYSTEM_PROMPT with nothing appended.
        from khadbot.agent.coach import BASE_SYSTEM_PROMPT, build_system_prompt

        assert build_system_prompt(None) == BASE_SYSTEM_PROMPT

    def test_none_does_not_contain_guardrail(self):
        # The guardrail references "a persona voice below" — must not appear
        # in the no-persona prompt where nothing follows.
        from khadbot.agent.coach import build_system_prompt

        result = build_system_prompt(None)
        assert "PERSONA SCOPE" not in result.upper()

    def test_returns_string(self, minimal_persona):
        from khadbot.agent.coach import build_system_prompt

        result = build_system_prompt(minimal_persona)
        assert isinstance(result, str)

    def test_base_prompt_is_present(self, minimal_persona):
        from khadbot.agent.coach import BASE_SYSTEM_PROMPT, build_system_prompt

        result = build_system_prompt(minimal_persona)
        # Strip both to avoid trailing-whitespace false negatives
        assert BASE_SYSTEM_PROMPT.strip() in result

    def test_voice_prompt_is_present(self, minimal_persona):
        from khadbot.agent.coach import build_system_prompt

        result = build_system_prompt(minimal_persona)
        assert minimal_persona.voice_prompt.strip() in result

    def test_base_prompt_comes_before_voice_prompt(self, minimal_persona):
        from khadbot.agent.coach import BASE_SYSTEM_PROMPT, build_system_prompt

        result = build_system_prompt(minimal_persona)
        base_pos = result.index(BASE_SYSTEM_PROMPT.strip())
        voice_pos = result.index(minimal_persona.voice_prompt.strip())
        assert base_pos < voice_pos, (
            "BASE_SYSTEM_PROMPT must appear before the persona voice_prompt. "
            "The model must encounter coaching scope rules before persona voice instructions."
        )

    def test_guardrail_present_between_base_and_voice(self, minimal_persona):
        from khadbot.agent.coach import BASE_SYSTEM_PROMPT, PERSONA_SCOPE_GUARDRAIL, build_system_prompt

        result = build_system_prompt(minimal_persona)
        base_end = result.index(BASE_SYSTEM_PROMPT.strip()) + len(BASE_SYSTEM_PROMPT.strip())
        voice_start = result.index(minimal_persona.voice_prompt.strip())
        between = result[base_end:voice_start]
        assert PERSONA_SCOPE_GUARDRAIL.strip() in between, (
            "PERSONA_SCOPE_GUARDRAIL must appear between base prompt and voice prompt."
        )

    def test_each_persona_produces_distinct_prompt(self):
        from khadbot.agent.coach import build_system_prompt
        from khadbot.agent.personas import list_personas

        prompts = [build_system_prompt(p) for p in list_personas()]
        assert len(prompts) == len(set(prompts)), "Each persona should produce a distinct assembled system prompt."

    def test_base_content_identical_across_all_personas(self):
        from khadbot.agent.coach import BASE_SYSTEM_PROMPT, build_system_prompt
        from khadbot.agent.personas import list_personas

        for persona in list_personas():
            result = build_system_prompt(persona)
            assert BASE_SYSTEM_PROMPT.strip() in result, (
                f"Persona '{persona.id}': BASE_SYSTEM_PROMPT content is missing or altered in the assembled prompt."
            )

    def test_voice_prompt_not_duplicated(self, minimal_persona):
        from khadbot.agent.coach import build_system_prompt

        result = build_system_prompt(minimal_persona)
        voice = minimal_persona.voice_prompt.strip()
        assert result.count(voice) == 1, "Voice prompt appears more than once in assembled prompt."


# ===========================================================================
# coach.py — build_agent_executor() persona wiring
# ===========================================================================


class TestBuildAgentExecutorPersonaWiring:
    """
    Verify that build_agent_executor() passes the correct assembled system
    prompt to create_agent. No LLM calls — create_agent and get_llm are mocked.
    """

    @pytest.fixture(autouse=True)
    def mock_dependencies(self, monkeypatch):
        """
        Patch create_agent and get_llm for the entire test class.
        Each test can inspect what create_agent was called with via self.mock_create_agent.
        """
        self.mock_llm = MagicMock()
        self.mock_agent = MagicMock()
        self.mock_create_agent = MagicMock(return_value=self.mock_agent)

        monkeypatch.setattr("khadbot.agent.coach.create_agent", self.mock_create_agent)
        monkeypatch.setattr("khadbot.llm_factory.get_llm", lambda: self.mock_llm)

    def _get_system_prompt_kwarg(self):
        """Extract the system_prompt passed to create_agent."""
        call_kwargs = self.mock_create_agent.call_args.kwargs
        return call_kwargs.get("system_prompt", "")

    def test_create_agent_called_once(self, minimal_persona):
        from khadbot.agent.coach import build_agent_executor

        build_agent_executor(persona=minimal_persona)
        self.mock_create_agent.assert_called_once()

    def test_explicit_persona_voice_prompt_in_system_prompt(self, minimal_persona):
        from khadbot.agent.coach import build_agent_executor

        build_agent_executor(persona=minimal_persona)
        system_prompt = self._get_system_prompt_kwarg()
        assert minimal_persona.voice_prompt.strip() in system_prompt

    def test_base_prompt_in_system_prompt(self, minimal_persona):
        from khadbot.agent.coach import BASE_SYSTEM_PROMPT, build_agent_executor

        build_agent_executor(persona=minimal_persona)
        system_prompt = self._get_system_prompt_kwarg()
        assert BASE_SYSTEM_PROMPT.strip() in system_prompt

    def test_base_prompt_before_voice_prompt_in_system_prompt(self, minimal_persona):
        from khadbot.agent.coach import BASE_SYSTEM_PROMPT, build_agent_executor

        build_agent_executor(persona=minimal_persona)
        system_prompt = self._get_system_prompt_kwarg()
        base_pos = system_prompt.index(BASE_SYSTEM_PROMPT.strip())
        voice_pos = system_prompt.index(minimal_persona.voice_prompt.strip())
        assert base_pos < voice_pos

    def test_llm_passed_to_create_agent(self, minimal_persona):
        from khadbot.agent.coach import build_agent_executor

        build_agent_executor(persona=minimal_persona)
        call_kwargs = self.mock_create_agent.call_args.kwargs
        assert call_kwargs.get("model") is self.mock_llm

    def test_tools_passed_to_create_agent(self, minimal_persona, monkeypatch):
        from khadbot.agent.coach import build_agent_executor

        # Patch TOOLS to a known sentinel so we can assert identity
        sentinel_tools = [MagicMock(name="tool_sentinel")]
        monkeypatch.setattr("khadbot.agent.coach.TOOLS", sentinel_tools)

        build_agent_executor(persona=minimal_persona)
        call_kwargs = self.mock_create_agent.call_args.kwargs
        assert call_kwargs.get("tools") is sentinel_tools

    def test_returns_agent_from_create_agent(self, minimal_persona):
        from khadbot.agent.coach import build_agent_executor

        result = build_agent_executor(persona=minimal_persona)
        assert result is self.mock_agent

    def test_different_personas_produce_different_system_prompts(self):
        from khadbot.agent.coach import build_agent_executor
        from khadbot.agent.personas import KHADGAR, THRALL

        build_agent_executor(persona=THRALL)
        thrall_prompt = self._get_system_prompt_kwarg()

        build_agent_executor(persona=KHADGAR)
        khadgar_prompt = self._get_system_prompt_kwarg()

        assert thrall_prompt != khadgar_prompt


class TestBuildAgentExecutorConfigFallback:
    """
    When no persona is passed, build_agent_executor() should resolve the
    persona from config (KHADBOT_PERSONA env var → get_persona()).
    """

    @pytest.fixture(autouse=True)
    def mock_dependencies(self, monkeypatch):
        self.mock_llm = MagicMock()
        self.mock_agent = MagicMock()
        self.mock_create_agent = MagicMock(return_value=self.mock_agent)

        monkeypatch.setattr("khadbot.agent.coach.create_agent", self.mock_create_agent)
        monkeypatch.setattr("khadbot.llm_factory.get_llm", lambda: self.mock_llm)

        # Reset config singleton so env var changes take effect
        import khadbot.config as config

        config.reset_config()
        yield
        config.reset_config()

    def _get_system_prompt_kwarg(self):
        return self.mock_create_agent.call_args.kwargs.get("system_prompt", "")

    def test_no_persona_arg_no_env_var_uses_base_prompt_only(self, monkeypatch):
        # No persona arg + no env var = base prompt only, no voice block appended.
        from khadbot.agent.coach import BASE_SYSTEM_PROMPT, build_agent_executor

        monkeypatch.delenv("KHADBOT_PERSONA", raising=False)
        build_agent_executor()
        system_prompt = self._get_system_prompt_kwarg()
        assert system_prompt == BASE_SYSTEM_PROMPT

    def test_khadbot_persona_env_var_selects_persona(self, monkeypatch):
        from khadbot.agent.coach import build_agent_executor
        from khadbot.agent.personas import THRALL

        monkeypatch.setenv("KHADBOT_PERSONA", "thrall")
        import khadbot.config as config

        config.reset_config()

        build_agent_executor()
        system_prompt = self._get_system_prompt_kwarg()
        assert THRALL.voice_prompt.strip() in system_prompt

    def test_unknown_env_var_uses_base_prompt_only(self, monkeypatch):
        # Unknown persona ID in env var → no persona active → base prompt only.
        from khadbot.agent.coach import BASE_SYSTEM_PROMPT, build_agent_executor

        monkeypatch.setenv("KHADBOT_PERSONA", "gandalf_the_grey")
        import khadbot.config as config

        config.reset_config()

        build_agent_executor()
        system_prompt = self._get_system_prompt_kwarg()
        assert system_prompt == BASE_SYSTEM_PROMPT

    def test_explicit_persona_arg_overrides_env_var(self, monkeypatch):
        # Explicit arg should win even if env var points to a different persona.
        from khadbot.agent.coach import build_agent_executor
        from khadbot.agent.personas import XALATATH

        monkeypatch.setenv("KHADBOT_PERSONA", "thrall")
        import khadbot.config as config

        config.reset_config()

        build_agent_executor(persona=XALATATH)
        system_prompt = self._get_system_prompt_kwarg()
        assert XALATATH.voice_prompt.strip() in system_prompt
