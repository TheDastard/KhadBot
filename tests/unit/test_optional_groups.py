"""
tests/unit/test_optional_groups.py

A feature whose optional libraries are missing fails with an error that names the dependency group to install. The
missing library is simulated by putting None in ``sys.modules``, which makes its import raise ImportError.
"""

from __future__ import annotations

import sys

import pytest

from khadbot import wiring
from khadbot.config import reset_config
from khadbot.errors import ConfigurationError, missing_group_message
from khadbot.llm_factory import LLMProviderError, get_llm
from khadbot.main import main
from khadbot.storage import default_blob_store


def test_message_names_the_group_and_the_install_command():
    message = missing_group_message("hosted", "Hosted mode")
    assert "Hosted mode needs the 'hosted' dependency group" in message
    assert "uv sync --extra hosted" in message


def test_serve_without_the_api_group(monkeypatch):
    monkeypatch.setitem(sys.modules, "uvicorn", None)
    with pytest.raises(SystemExit, match="'api' dependency group"):
        main(["serve"])


def test_migrate_without_the_hosted_group(monkeypatch):
    monkeypatch.setitem(sys.modules, "khadbot.db", None)
    with pytest.raises(SystemExit, match="'hosted' dependency group"):
        main(["migrate"])


def test_hosted_mode_without_the_hosted_group(monkeypatch):
    monkeypatch.setenv("KHADBOT_MODE", "hosted")
    monkeypatch.setitem(sys.modules, "khadbot.db", None)
    with pytest.raises(ConfigurationError, match="'hosted' dependency group"):
        wiring.findings_store()
    with pytest.raises(ConfigurationError, match="'hosted' dependency group"):
        wiring.read_model_projector()


def test_s3_storage_without_the_hosted_group(monkeypatch):
    monkeypatch.setenv("KHADBOT_STORAGE", "s3")
    monkeypatch.setenv("KHADBOT_S3_BUCKET", "bucket")
    monkeypatch.setitem(sys.modules, "khadbot.storage.s3", None)
    with pytest.raises(ConfigurationError, match="'hosted' dependency group"):
        default_blob_store()


@pytest.mark.parametrize(
    ("provider", "module", "key"),
    [
        ("openai", "langchain_openai", "OPENAI_API_KEY"),
        ("groq", "langchain_groq", "GROQ_API_KEY"),
    ],
)
def test_provider_without_the_providers_group(monkeypatch, provider, module, key):
    monkeypatch.setenv(key, "fake-key")
    reset_config()
    monkeypatch.setitem(sys.modules, module, None)
    with pytest.raises(LLMProviderError, match="'providers' dependency group"):
        get_llm(provider)
