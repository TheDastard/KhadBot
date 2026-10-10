"""
tests/unit/test_llm_factory.py

Role-aware provider/model resolution in get_llm(). Provider client classes are
patched; no network and no real model is built.
"""

from unittest.mock import patch

import pytest

from khadbot.config import reset_config
from khadbot.llm_factory import get_llm


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for key in (
        "LLM_PROVIDER_ANALYST",
        "LLM_PROVIDER_CONVERSATION",
        "ANALYST_MODEL",
        "CONVERSATION_MODEL",
        "GROQ_MODEL",
        "OPENAI_MODEL",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "groq")
    monkeypatch.setenv("GROQ_API_KEY", "g-key")
    monkeypatch.setenv("OPENAI_API_KEY", "o-key")
    reset_config()
    yield
    reset_config()


class TestRoleResolution:
    def test_no_role_uses_global_provider(self):
        with patch("langchain_groq.ChatGroq") as groq:
            get_llm()
        groq.assert_called_once()

    def test_role_provider_beats_global(self, monkeypatch):
        monkeypatch.setenv("LLM_PROVIDER_ANALYST", "openai")
        reset_config()
        with patch("langchain_openai.ChatOpenAI") as openai, patch("langchain_groq.ChatGroq") as groq:
            get_llm(role="analyst")
        openai.assert_called_once()
        groq.assert_not_called()

    def test_explicit_provider_beats_role(self, monkeypatch):
        monkeypatch.setenv("LLM_PROVIDER_ANALYST", "openai")
        reset_config()
        with patch("langchain_groq.ChatGroq") as groq, patch("langchain_openai.ChatOpenAI") as openai:
            get_llm("groq", role="analyst")
        groq.assert_called_once()
        openai.assert_not_called()

    def test_unknown_role_falls_back(self, monkeypatch):
        monkeypatch.setenv("LLM_PROVIDER_ANALYST", "openai")
        reset_config()
        with patch("langchain_groq.ChatGroq") as groq:
            get_llm(role="mystery")
        groq.assert_called_once()

    def test_role_model_override_applies(self, monkeypatch):
        monkeypatch.setenv("ANALYST_MODEL", "custom-70b")
        reset_config()
        with patch("langchain_groq.ChatGroq") as groq:
            get_llm(role="analyst")
        assert groq.call_args.kwargs["model"] == "custom-70b"

    def test_role_model_not_applied_to_other_roles(self, monkeypatch):
        monkeypatch.setenv("ANALYST_MODEL", "custom-70b")
        reset_config()
        with patch("langchain_groq.ChatGroq") as groq:
            get_llm(role="conversation")
        assert groq.call_args.kwargs["model"] != "custom-70b"
