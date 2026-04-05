"""
tests/unit/agent/test_router.py

Unit tests for agent/router.py.

Coverage: router_node — classification, JSON parsing, hallucination guard,
fence stripping, empty-task and empty-registry short-circuits.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from khadbot.agent.router import router_node


class TestRouterNode:
    @pytest.mark.asyncio
    async def test_happy_path_single_skill(self, make_llm, make_state, make_skill):
        registry = {"personal_coaching": make_skill("personal_coaching")}
        llm = make_llm(response='["personal_coaching"]')
        state = make_state(task="Why is my DPS low?")

        with (
            patch("khadbot.agent.router.SKILL_REGISTRY", registry),
            patch("khadbot.agent.router.get_llm", return_value=llm),
        ):
            result = await router_node(state)

        assert result["needed_skills"] == ["personal_coaching"]

    @pytest.mark.asyncio
    async def test_hallucinated_skill_name_dropped(self, make_llm, make_state, make_skill):
        registry = {"personal_coaching": make_skill("personal_coaching")}
        llm = make_llm(response='["personal_coaching", "ghost_skill"]')
        state = make_state(task="analyse my logs")

        with (
            patch("khadbot.agent.router.SKILL_REGISTRY", registry),
            patch("khadbot.agent.router.get_llm", return_value=llm),
        ):
            result = await router_node(state)

        assert result["needed_skills"] == ["personal_coaching"]
        assert "ghost_skill" not in result["needed_skills"]

    @pytest.mark.asyncio
    async def test_all_hallucinated_returns_empty(self, make_llm, make_state, make_skill):
        registry = {"personal_coaching": make_skill("personal_coaching")}
        llm = make_llm(response='["ghost1", "ghost2"]')
        state = make_state(task="something")

        with (
            patch("khadbot.agent.router.SKILL_REGISTRY", registry),
            patch("khadbot.agent.router.get_llm", return_value=llm),
        ):
            result = await router_node(state)

        assert result["needed_skills"] == []

    @pytest.mark.asyncio
    async def test_json_parse_failure_returns_empty(self, make_llm, make_state, make_skill):
        registry = {"personal_coaching": make_skill("personal_coaching")}
        llm = make_llm(response="I recommend personal_coaching")
        state = make_state(task="some question")

        with (
            patch("khadbot.agent.router.SKILL_REGISTRY", registry),
            patch("khadbot.agent.router.get_llm", return_value=llm),
        ):
            result = await router_node(state)

        assert result["needed_skills"] == []

    @pytest.mark.asyncio
    async def test_markdown_fence_stripped(self, make_llm, make_state, make_skill):
        registry = {"build_review": make_skill("build_review")}
        llm = make_llm(response='```json\n["build_review"]\n```')
        state = make_state(task="what talents should I use?")

        with (
            patch("khadbot.agent.router.SKILL_REGISTRY", registry),
            patch("khadbot.agent.router.get_llm", return_value=llm),
        ):
            result = await router_node(state)

        assert result["needed_skills"] == ["build_review"]

    @pytest.mark.asyncio
    async def test_empty_array_response_valid(self, make_llm, make_state, make_skill):
        registry = {"personal_coaching": make_skill("personal_coaching")}
        llm = make_llm(response="[]")
        state = make_state(task="what time is it?")

        with (
            patch("khadbot.agent.router.SKILL_REGISTRY", registry),
            patch("khadbot.agent.router.get_llm", return_value=llm),
        ):
            result = await router_node(state)

        assert result["needed_skills"] == []

    @pytest.mark.asyncio
    async def test_empty_task_skips_llm_call(self, make_llm, make_state, make_skill):
        registry = {"personal_coaching": make_skill("personal_coaching")}
        llm = make_llm()
        state = make_state(task="")

        with (
            patch("khadbot.agent.router.SKILL_REGISTRY", registry),
            patch("khadbot.agent.router.get_llm", return_value=llm),
        ):
            result = await router_node(state)

        assert result["needed_skills"] == []
        llm.invoke.assert_not_called()

    @pytest.mark.asyncio
    async def test_empty_registry_skips_llm_call(self, make_llm, make_state):
        llm = make_llm()
        state = make_state(task="why did we wipe?")

        with patch("khadbot.agent.router.SKILL_REGISTRY", {}), patch("khadbot.agent.router.get_llm", return_value=llm):
            result = await router_node(state)

        assert result["needed_skills"] == []
        llm.invoke.assert_not_called()

    @pytest.mark.asyncio
    async def test_multiple_valid_skills_returned(self, make_llm, make_state, make_skill):
        registry = {
            "personal_coaching": make_skill("personal_coaching"),
            "build_review": make_skill("build_review"),
        }
        llm = make_llm(response='["personal_coaching", "build_review"]')
        state = make_state(task="analyse and advise")

        with (
            patch("khadbot.agent.router.SKILL_REGISTRY", registry),
            patch("khadbot.agent.router.get_llm", return_value=llm),
        ):
            result = await router_node(state)

        assert set(result["needed_skills"]) == {"personal_coaching", "build_review"}
