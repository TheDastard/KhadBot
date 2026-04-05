"""
tests/unit/agent/conftest.py

Shared fixtures for agent unit tests.

All helpers are fixtures — no module-level functions, no imports in test files.
Test methods declare fixtures as parameters and pytest injects them.

Fixtures
--------
make_llm_response   — factory: (content) → MagicMock with .content attribute
make_llm            — factory: (response, capture, side_effect) →  AsyncMock with ainvoke method
make_state          — factory: (**kwargs) → KhadbotState
make_skill          — factory: (name, result="analysis result") → SkillDefinition

Usage in a test method:

    async def test_something(self, make_llm, make_state, make_skill):
        llm = make_llm(response="Greate advice!")
        skill = make_skill("personal_coaching", result="72nd percentile")
        state = make_state(task="Why is my DPS low?")
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from khadbot.agent.skills import SkillDefinition
from khadbot.agent.state import KhadbotState

# ---------------------------------------------------------------------------
# LLM response factory
# ---------------------------------------------------------------------------


@pytest.fixture
def make_llm_response():
    """
    Factory: build a minimal fake LLM response with a .content attribute.

        llm.ainvoke = AsyncMock(return_value=make_llm_response("Great advice!"))
    """

    def _make(content: str) -> MagicMock:
        msg = MagicMock()
        msg.content = content
        return msg

    return _make


# ---------------------------------------------------------------------------
# LLM factory
# ---------------------------------------------------------------------------


@pytest.fixture()
def make_llm(make_llm_response):
    """
    Factory: async LLM mock with optional call capture and side effect.

    Simple use — scripted response:
        llm = make_llm()
        llm = make_llm(response="specific response")

    Inspection use — capture what the LLM was passed:
        captured = []
        llm = make_llm(capture=captured)
        # after the call:
        system_prompt = captured[0].content

    Failure use — simulate LLM errors:
        llm = make_llm(side_effect=RuntimeError("LLM unreachable"))
    """

    def _make(
        response: str = "LLM response",
        capture: list | None = None,
        side_effect: BaseException | None = None,
    ) -> AsyncMock:
        async def _ainvoke(messages):
            if side_effect is not None:
                raise side_effect
            if capture is not None:
                capture.extend(messages)
            return make_llm_response(response)

        llm = AsyncMock()
        llm.ainvoke = _ainvoke
        return llm

    return _make


# ---------------------------------------------------------------------------
# State factory
# ---------------------------------------------------------------------------


@pytest.fixture
def make_state():
    """
    Factory: build a KhadbotState dict with sensible defaults.

        state = make_state(task="Why is my DPS low?")
        state = make_state(active_skills={"personal_coaching": skill})
    """

    def _make(**kwargs: Any) -> KhadbotState:
        base: dict[str, Any] = {
            "messages": [],
            "task": "",
            "needed_skills": [],
            "active_skills": {},
            "skill_results": {},
            "character_context": None,
            "persona_id": None,
        }
        base.update(kwargs)
        return base  # type: ignore[return-value]

    return _make


# ---------------------------------------------------------------------------
# Skill factory
# ---------------------------------------------------------------------------


@pytest.fixture
def make_skill():
    """
    Factory: build a SkillDefinition with a scripted async subgraph.

        skill = make_skill("personal_coaching")
        skill = make_skill("encounter_review", result="wipe analysis")

    The subgraph's ainvoke returns {"result": result} by default.
    Tests that need custom subgraph behaviour should set subgraph.ainvoke
    directly on the returned SkillDefinition.
    """

    def _make(name: str, result: str = "analysis result") -> SkillDefinition:
        subgraph = AsyncMock()
        subgraph.ainvoke = AsyncMock(return_value={"result": result})
        return SkillDefinition(
            name=name,
            display_name=name,
            description=f"{name} one-liner description",
            routing_description=f"{name} longer routing description",
            tools=[],
            subgraph=subgraph,
        )

    return _make
