"""
tests/conftest.py

Shared pytest fixtures available to all test modules.
Fixtures here are automatically discovered by pytest — no imports needed.
"""

import httpx
import pytest
import respx as _respx
from fixtures.agent_payloads import MOCK_RAIDERIO_RESULT, RAIDERIO_THEN_ANSWER
from fixtures.raiderio_payloads import CHARACTER_NOT_FOUND_BODY, MAGE_PROFILE_RAW
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import AIMessage

# ---------------------------------------------------------------------------
# LLM fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_llm_raiderio():
    """FakeListChatModel scripted to call get_character_raiderio then answer."""
    return FakeListChatModel(responses=RAIDERIO_THEN_ANSWER)


@pytest.fixture
def fake_llm_single_answer():
    """FakeListChatModel that returns a single canned answer with no tool calls."""
    return FakeListChatModel(responses=[AIMessage(content="Here is your coaching advice.")])


# ---------------------------------------------------------------------------
# Raider.IO HTTP fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_raiderio_success():
    """
    Context manager that mocks the Raider.IO profile endpoint with a 200 response.
    Usage:
        def test_something(mock_raiderio_success):
            with mock_raiderio_success:
                result = ...
    """
    with _respx.mock:
        _respx.get("https://raider.io/api/v1/characters/profile").mock(
            return_value=httpx.Response(200, json=MAGE_PROFILE_RAW)
        )
        yield


@pytest.fixture
def mock_raiderio_not_found():
    """Mocks Raider.IO returning 404."""
    with _respx.mock:
        _respx.get("https://raider.io/api/v1/characters/profile").mock(
            return_value=httpx.Response(404, json=CHARACTER_NOT_FOUND_BODY)
        )
        yield


@pytest.fixture
def mock_raiderio_server_error():
    """Mocks Raider.IO returning 500."""
    with _respx.mock:
        _respx.get("https://raider.io/api/v1/characters/profile").mock(
            return_value=httpx.Response(500, json={"message": "Internal Server Error"})
        )
        yield


# ---------------------------------------------------------------------------
# Realistic raw API response
# ---------------------------------------------------------------------------


@pytest.fixture
def mage_profile_raw():
    return MAGE_PROFILE_RAW.copy()


@pytest.fixture
def mock_raiderio_result():
    return MOCK_RAIDERIO_RESULT.copy()
