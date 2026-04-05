"""
tests/unit/agent/subgraphs/test_character_sim.py

Unit tests for agent/subgraphs/character_sim.py.

Tests the deterministic (non-LLM) nodes only:
  check_simc_input_node  — pattern detection, status transitions
  request_simc_string_node — response content
  confirmation_gate_node — prototype pass-through behaviour
  route_after_check      — routing function
  route_after_confirmation — routing function

synthesise_node and run_sim_node are async and LLM/subprocess dependent —
they are exercised in integration tests.
"""

from __future__ import annotations

from khadbot.agent.subgraphs.character_sim import (
    _REQUEST_SIMC_RESPONSE,
    CharacterSimState,
    check_simc_input_node,
    confirmation_gate_node,
    request_simc_string_node,
    route_after_check,
    route_after_confirmation,
)


def _sim_state(**kwargs) -> CharacterSimState:
    base = {
        "task": "",
        "character_context": None,
        "messages": [],
        "result": "",
        "simc_string": None,
        "sim_output": None,
        "simc_status": "missing",
    }
    base.update(kwargs)
    return base  # type: ignore[return-value]


# Valid SimC export header pattern: ClassName="Name", level=70, ...
_VALID_SIMC_HEADER = 'EnhancementShaman="Thrall", level=70, race=Orc, region=us, server=stormrage'
_VALID_SIMC_BODY = "\nspec=enhancement\nrole=attack\n"


class TestCheckSimcInputNode:
    def test_valid_simc_string_detected(self):
        state = _sim_state(task=f"Please sim this:\n{_VALID_SIMC_HEADER}{_VALID_SIMC_BODY}")
        result = check_simc_input_node(state)
        assert result["simc_status"] == "pending_confirmation"
        assert result["simc_string"] is not None
        assert "EnhancementShaman" in result["simc_string"]

    def test_simc_string_extracted_from_match_start(self):
        preamble = "Here is my simc export:\n"
        task = preamble + _VALID_SIMC_HEADER + _VALID_SIMC_BODY
        state = _sim_state(task=task)
        result = check_simc_input_node(state)
        # Should NOT include the preamble
        assert "Here is my simc" not in result["simc_string"]
        assert _VALID_SIMC_HEADER in result["simc_string"]

    def test_no_simc_string_sets_missing_status(self):
        state = _sim_state(task="Can you sim my character?")
        result = check_simc_input_node(state)
        assert result["simc_status"] == "missing"
        assert result["simc_string"] is None

    def test_empty_task_sets_missing_status(self):
        state = _sim_state(task="")
        result = check_simc_input_node(state)
        assert result["simc_status"] == "missing"
        assert result["simc_string"] is None

    def test_case_insensitive_detection(self):
        lower_header = 'enhancementshaman="Thrall", level=70'
        state = _sim_state(task=lower_header)
        result = check_simc_input_node(state)
        assert result["simc_status"] == "pending_confirmation"

    def test_partial_match_not_detected(self):
        state = _sim_state(task="level=70 is my character level")
        result = check_simc_input_node(state)
        # No class="Name" before the level= — should not match
        assert result["simc_status"] == "missing"


class TestRequestSimcStringNode:
    def test_returns_request_response(self):
        state = _sim_state()
        result = request_simc_string_node(state)
        assert result["result"] == _REQUEST_SIMC_RESPONSE

    def test_response_contains_addon_instructions(self):
        state = _sim_state()
        result = request_simc_string_node(state)
        assert "SimulationCraft" in result["result"]
        assert "Export" in result["result"]


class TestConfirmationGateNode:
    def test_prototype_proceeds_automatically(self):
        state = _sim_state(simc_status="pending_confirmation")
        result = confirmation_gate_node(state)
        assert result["simc_status"] == "running"

    def test_does_not_modify_simc_string(self):
        state = _sim_state(
            simc_status="pending_confirmation",
            simc_string=_VALID_SIMC_HEADER,
        )
        result = confirmation_gate_node(state)
        assert "simc_string" not in result


class TestRouteAfterCheck:
    def test_missing_routes_to_request(self):
        state = _sim_state(simc_status="missing")
        assert route_after_check(state) == "request_simc_string"

    def test_pending_confirmation_routes_to_gate(self):
        state = _sim_state(simc_status="pending_confirmation")
        assert route_after_check(state) == "confirmation_gate"

    def test_default_routes_to_request(self):
        state = _sim_state()  # simc_status defaults to "missing"
        assert route_after_check(state) == "request_simc_string"


class TestRouteAfterConfirmation:
    def test_always_routes_to_run_sim(self):
        state = _sim_state(simc_status="running")
        assert route_after_confirmation(state) == "run_sim"
