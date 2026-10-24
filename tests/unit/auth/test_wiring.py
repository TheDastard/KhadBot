"""
tests/unit/auth/test_wiring.py

How the composition root and the API pick up sign-in: local mode has none, hosted mode without the ``hosted`` group
names it, and ``create_app(auth=...)`` mounts the sign-in routes next to the read routes.
"""

from __future__ import annotations

import sys

import pytest
from fastapi.testclient import TestClient

from khadbot import wiring
from khadbot.api import create_app
from khadbot.errors import ConfigurationError


def test_local_mode_has_no_sign_in(monkeypatch):
    monkeypatch.delenv("KHADBOT_MODE", raising=False)
    assert wiring.auth_service() is None
    assert wiring.user_tokens() is None
    assert wiring.report_access() is None


def test_hosted_mode_without_the_hosted_group(monkeypatch):
    monkeypatch.setenv("KHADBOT_MODE", "hosted")
    monkeypatch.setitem(sys.modules, "khadbot.auth.pg", None)
    with pytest.raises(ConfigurationError, match="'hosted' dependency group"):
        wiring.auth_service()


def test_local_app_has_no_auth_routes(tmp_path):
    with TestClient(create_app(data_dir=tmp_path)) as client:
        assert client.get("/v1/me").status_code == 404
        assert client.get("/v1/health").status_code == 200


def test_create_app_mounts_sign_in(tmp_path, service):
    with TestClient(create_app(data_dir=tmp_path, auth=service), base_url="http://localhost:8000") as client:
        assert client.get("/v1/me").json()["code"] == "not_signed_in"
        assert client.get("/v1/auth/login", follow_redirects=False).status_code == 303
        assert client.get("/v1/health").status_code == 200
