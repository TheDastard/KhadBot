"""
tests/unit/auth/test_service.py

``AuthService`` construction (fail-fast configuration) and the ``next`` path check that prevents open redirects.
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from helpers.auth.fake_wcl import FAKE_CLIENT_ID, FAKE_CLIENT_SECRET

from khadbot.auth.service import AuthService, safe_next_path
from khadbot.auth.store import MemoryAuthStore
from khadbot.config import AuthConfig, is_local_http
from khadbot.errors import ConfigurationError


def build(cfg, client_id=FAKE_CLIENT_ID, client_secret=FAKE_CLIENT_SECRET):
    return AuthService(MemoryAuthStore(), cfg, client_id=client_id, client_secret=client_secret)


def test_plain_http_off_localhost_is_refused(auth_config):
    with pytest.raises(ConfigurationError, match="https"):
        build(replace(auth_config, redirect_uri="http://khadbot.example.test/v1/auth/callback"))


def test_missing_token_keys(auth_config):
    with pytest.raises(ConfigurationError, match="KHADBOT_TOKEN_KEYS"):
        build(replace(auth_config, token_keys=()))


def test_missing_client_credentials(auth_config):
    with pytest.raises(ConfigurationError, match="WARCRAFTLOGS_CLIENT_SECRET"):
        build(auth_config, client_secret="")


def test_repr_hides_everything(auth_config, fernet_key):
    service = build(auth_config)
    assert fernet_key not in repr(service) and FAKE_CLIENT_SECRET not in repr(service)
    assert fernet_key not in repr(auth_config)


@pytest.mark.parametrize(
    ("url", "local"),
    [
        ("http://localhost:8000/v1/auth/callback", True),
        ("http://127.0.0.1/cb", True),
        ("http://[::1]:8000/cb", True),
        ("https://localhost/cb", False),
        ("http://localhost.evil.test/cb", False),
        ("https://khadbot.example.test/cb", False),
    ],
)
def test_is_local_http(url, local):
    assert is_local_http(url) is local
    assert AuthConfig(redirect_uri=url).cookie_secure is (not local)


def test_api_origin():
    assert AuthConfig(redirect_uri="https://api.example.test/v1/auth/callback").api_origin == "https://api.example.test"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, "/"),
        ("/reports/ABC", "/reports/ABC"),
        ("/reports/ABC?fight=3", "/reports/ABC?fight=3"),
        ("//evil.example", "/"),
        ("https://evil.example", "/"),
        ("/\\evil.example", "/"),
        ("reports", "/"),
        ("/a b", "/"),
        ("/" + "x" * 600, "/"),
    ],
)
def test_safe_next_path(value, expected):
    assert safe_next_path(value) == expected
