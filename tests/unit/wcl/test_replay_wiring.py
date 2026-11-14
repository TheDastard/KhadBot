"""
tests/unit/wcl/test_replay_wiring.py

``wiring.announce_wcl_replay``: WCL replay, a development switch, stops a hosted deployment that is not on plain-HTTP
localhost from starting, because replay answers report access checks from the recordings.
"""

from __future__ import annotations

import pytest

from khadbot import wiring
from khadbot.errors import ConfigurationError


@pytest.fixture
def hosted(monkeypatch, tmp_path):
    monkeypatch.setenv("KHADBOT_MODE", "hosted")
    monkeypatch.setenv("DATABASE_URL", "postgresql://fake:fake@localhost/fake")
    monkeypatch.setenv("KHADBOT_WCL_REPLAY", str(tmp_path))
    monkeypatch.delenv("KHADBOT_WCL_REPLAY_LIVE", raising=False)


def test_replay_is_refused_on_a_deployment_that_is_not_local(hosted, monkeypatch):
    monkeypatch.setenv("KHADBOT_WCL_REDIRECT_URI", "https://api.example.test/v1/auth/callback")
    with pytest.raises(ConfigurationError, match="development switch"):
        wiring.announce_wcl_replay()


def test_replay_starts_on_plain_http_localhost(hosted, monkeypatch):
    monkeypatch.setenv("KHADBOT_WCL_REDIRECT_URI", "http://localhost:8000/v1/auth/callback")
    wiring.announce_wcl_replay()  # the local stack and the dev loop: allowed, and announced in the log


def test_nothing_happens_without_replay(monkeypatch):
    monkeypatch.delenv("KHADBOT_WCL_REPLAY", raising=False)
    monkeypatch.setenv("KHADBOT_MODE", "hosted")
    monkeypatch.setenv("KHADBOT_WCL_REDIRECT_URI", "https://api.example.test/v1/auth/callback")
    wiring.announce_wcl_replay()
