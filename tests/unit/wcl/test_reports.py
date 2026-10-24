"""
tests/unit/wcl/test_reports.py

``report_readable``: what a token can read, and which failures leave the answer unknown. HTTP is mocked with respx.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from khadbot.wcl.client import (
    GRAPHQL_CLIENT_URL,
    GRAPHQL_USER_URL,
    TOKEN_URL,
    WarcraftLogsClient,
    WarcraftLogsUnauthorizedError,
)
from khadbot.wcl.reports import ReportCheckUnavailableError, report_readable


@pytest.fixture
def router():
    with respx.mock(assert_all_called=False) as r:
        r.post(TOKEN_URL).mock(return_value=httpx.Response(200, json={"access_token": "fake-app", "expires_in": 3600}))
        yield r


async def _check(code="ABC", user_token=None):
    async with WarcraftLogsClient("cid", "secret") as wcl:
        return await report_readable(wcl, code, user_token=user_token)


def _report(value):
    return httpx.Response(200, json={"data": {"reportData": {"report": value}}})


async def test_readable_with_app_token(router):
    route = router.post(GRAPHQL_CLIENT_URL).mock(return_value=_report({"startTime": 1}))
    assert await _check() is True
    assert route.calls.last.request.read().decode().count('"code":"ABC"') == 1


async def test_user_token_goes_to_user_endpoint(router):
    route = router.post(GRAPHQL_USER_URL).mock(return_value=_report({"startTime": 1}))
    assert await _check(user_token="fake-user") is True
    assert route.calls.last.request.headers["Authorization"] == "Bearer fake-user"


async def test_null_report_is_unreadable(router):
    router.post(GRAPHQL_CLIENT_URL).mock(return_value=_report(None))
    assert await _check() is False


async def test_private_error_is_unreadable(router):
    router.post(GRAPHQL_CLIENT_URL).mock(
        return_value=httpx.Response(
            200, json={"errors": [{"message": "You do not have permission to view this private report"}]}
        )
    )
    assert await _check() is False


async def test_other_graphql_error_is_unreadable(router):
    router.post(GRAPHQL_CLIENT_URL).mock(
        return_value=httpx.Response(200, json={"errors": [{"message": "This report does not exist."}]})
    )
    assert await _check() is False


async def test_http_error_is_unknown(router):
    router.post(GRAPHQL_CLIENT_URL).mock(return_value=httpx.Response(502))
    with pytest.raises(ReportCheckUnavailableError):
        await _check()


async def test_rate_limit_is_unknown(router):
    router.post(GRAPHQL_CLIENT_URL).mock(return_value=httpx.Response(429))
    with pytest.raises(ReportCheckUnavailableError):
        await _check()


async def test_rejected_user_token_propagates(router):
    router.post(GRAPHQL_USER_URL).mock(return_value=httpx.Response(401))
    with pytest.raises(WarcraftLogsUnauthorizedError):
        await _check(user_token="fake-user")
