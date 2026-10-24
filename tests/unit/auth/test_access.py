"""
tests/unit/auth/test_access.py

``ReportAccess.can_read``: the user's token first, the app token for public reports, cached answers that are
re-checked after their age, and nothing cached when WCL can't answer. WCL is faked with respx.
"""

from __future__ import annotations

import httpx
import pytest
from pydantic import SecretStr

from khadbot.auth.errors import ReportNotFoundError, WclUnavailableError
from khadbot.wcl.client import GRAPHQL_CLIENT_URL
from khadbot.wcl.oauth import TokenSet


@pytest.fixture
async def user(service, store, clock, wcl):
    wcl.users["fake-access-1"] = (777, "Khadgar")
    wcl.issued = 1  # the next token WCL issues is fake-access-2
    created = store.upsert_user(777, "Khadgar", clock.now)
    await service.tokens.save(created.id, TokenSet(SecretStr("fake-access-1"), SecretStr("fake-refresh-1"), 3600))
    return created.id


@pytest.fixture
def access(service):
    return service.access


async def test_user_token_reads_a_private_report(access, wcl, user, store):
    wcl.readable["fake-access-1"] = {"PRIV1"}
    assert await access.can_read(user, "PRIV1") is True
    assert wcl.report_checks == ["fake-access-1:PRIV1"]
    assert store.access[(user, "PRIV1")].via == "user"


async def test_private_report_of_someone_else(access, wcl, user, store):
    assert await access.can_read(user, "PRIV2") is False
    assert wcl.report_checks == ["fake-access-1:PRIV2", "app:PRIV2"]  # user token, then the public check
    assert store.access[(user, "PRIV2")].readable is False


async def test_public_report_falls_back_to_app_token(access, wcl, user, store):
    wcl.readable["app"] = {"PUB1"}
    assert await access.can_read(user, "PUB1") is True
    assert store.access[(user, "PUB1")].via == "app"


async def test_visitor_sees_public_only(access, wcl):
    wcl.readable["app"] = {"PUB1"}
    assert await access.can_read(None, "PUB1") is True
    assert await access.can_read(None, "PRIV1") is False
    assert all(check.startswith("app:") for check in wcl.report_checks)


async def test_answers_are_cached_then_rechecked(access, wcl, user, clock):
    wcl.readable["fake-access-1"] = {"PRIV1"}
    assert await access.can_read(user, "PRIV1")
    clock.advance(minutes=59)
    assert await access.can_read(user, "PRIV1")
    assert len(wcl.report_checks) == 1  # cached
    wcl.readable.clear()  # the report was deleted or made private since
    clock.advance(minutes=2)  # past the one-hour re-check age
    assert await access.can_read(user, "PRIV1") is False
    assert [c.split(":")[1] for c in wcl.report_checks[1:]] == ["PRIV1", "PRIV1"]  # asked WCL again
    assert wcl.report_checks[-1] == "app:PRIV1"


async def test_denials_are_rechecked_sooner(access, wcl, user, clock):
    assert not await access.can_read(user, "LATER1")
    checks = len(wcl.report_checks)
    clock.advance(minutes=4)
    assert not await access.can_read(user, "LATER1")
    assert len(wcl.report_checks) == checks
    wcl.readable["fake-access-1"] = {"LATER1"}  # shared with the user since
    clock.advance(minutes=2)
    assert await access.can_read(user, "LATER1")


async def test_cache_is_per_user(access, wcl, user, store, clock, service):
    wcl.readable["fake-access-1"] = {"PRIV1"}
    assert await access.can_read(user, "PRIV1")
    other = store.upsert_user(888, "Thrall", clock.now)
    await service.tokens.save(other.id, TokenSet(SecretStr("fake-access-50"), SecretStr("fake-refresh-50"), 3600))
    wcl.users["fake-access-50"] = (888, "Thrall")
    assert await access.can_read(other.id, "PRIV1") is False


async def test_expired_user_token_is_refreshed_during_the_check(access, wcl, user, clock):
    wcl.readable["fake-access-1"] = {"PRIV1"}
    clock.advance(hours=2)
    assert await access.can_read(user, "PRIV1") is True
    assert any(r["grant_type"] == ["refresh_token"] for r in wcl.token_requests)


async def test_rejected_user_token_is_refreshed_and_retried(access, wcl, user):
    wcl.readable["fake-access-1"] = {"PRIV1"}
    wcl.revoked.add("fake-access-1")  # WCL rejects it before its stated expiry
    assert await access.can_read(user, "PRIV1") is True
    assert wcl.report_checks == ["fake-access-2:PRIV1"]


async def test_user_without_tokens_gets_the_public_check(access, wcl, store, clock):
    lone = store.upsert_user(999, "Xal", clock.now)
    wcl.readable["app"] = {"PUB1"}
    assert await access.can_read(lone.id, "PUB1") is True


async def test_wcl_down_caches_nothing(access, wcl, user, store):
    wcl.router.post(GRAPHQL_CLIENT_URL).mock(return_value=httpx.Response(503))
    with pytest.raises(WclUnavailableError):
        await access.can_read(None, "ANY1")
    assert not store.public_access and not store.access


async def test_malformed_code_never_reaches_wcl(access, wcl, user):
    assert await access.can_read(user, "../etc/passwd") is False
    assert await access.can_read(user, "") is False
    assert not wcl.report_checks


async def test_require_readable(access, wcl, user):
    with pytest.raises(ReportNotFoundError) as exc:
        await access.require_readable(user, "PRIV9")
    assert exc.value.status == 404
