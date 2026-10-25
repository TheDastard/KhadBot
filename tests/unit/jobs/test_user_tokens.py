"""
tests/unit/jobs/test_user_tokens.py

``UserTokenResolver``: a ``UserAuth`` job fetches with the user's own token, loaded on every request; an unusable
sign-in fails the job before any work; the job spec and its errors never hold the token.
"""

from __future__ import annotations

import pytest

from khadbot.auth.errors import WclSignInRequiredError, WclUnavailableError
from khadbot.jobs import (
    AppAuth,
    IngestReport,
    JobError,
    UserAuth,
    UserAuthUnavailableError,
    UserTokenResolver,
    default_token_resolver,
)


class Tokens:
    """A token source that hands out ``fake-access-<n>``, counting calls."""

    def __init__(self, error: Exception | None = None) -> None:
        self.calls = 0
        self.error = error

    async def access_token(self, user_id: str, *, force_refresh: bool = False) -> str:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return f"fake-access-{self.calls}"


async def test_app_auth_never_builds_the_source():
    built = []
    resolver = UserTokenResolver(lambda: built.append(1) or Tokens())
    assert await resolver.provider_for(AppAuth()) is None
    assert not built


async def test_user_auth_reads_the_token_on_every_request():
    tokens = Tokens()
    provider = await UserTokenResolver(lambda: tokens).provider_for(UserAuth(user_id="u-1"))
    assert provider is not None
    assert await provider() == "fake-access-2"  # the first call checked the sign-in up front
    assert await provider() == "fake-access-3"


async def test_signed_out_user_fails_before_work():
    resolver = UserTokenResolver(lambda: Tokens(WclSignInRequiredError("revoked")))
    with pytest.raises(UserAuthUnavailableError, match="sign in again"):
        await resolver.provider_for(UserAuth(user_id="u-1"))


async def test_wcl_down_is_a_retryable_job_error():
    resolver = UserTokenResolver(lambda: Tokens(WclUnavailableError("down")))
    with pytest.raises(JobError) as exc:
        await resolver.provider_for(UserAuth(user_id="u-1"))
    assert not isinstance(exc.value, UserAuthUnavailableError)


async def test_local_mode_has_no_user_tokens(monkeypatch):
    monkeypatch.delenv("KHADBOT_MODE", raising=False)
    with pytest.raises(UserAuthUnavailableError, match="local mode"):
        await default_token_resolver().provider_for(UserAuth(user_id="u-1"))
    assert await default_token_resolver().provider_for(AppAuth()) is None


def test_spec_carries_only_the_user_id():
    spec = IngestReport(code="ABC", auth=UserAuth(user_id="u-1"))
    assert spec.model_dump() == {
        "job": "ingest_report",
        "code": "ABC",
        "fights": None,
        "auth": {"kind": "user", "user_id": "u-1"},
        "offline": False,
        "refresh_meta": False,
        "live": False,
    }
