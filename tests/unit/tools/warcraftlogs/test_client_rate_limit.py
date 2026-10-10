"""
tests/unit/tools/warcraftlogs/test_client_rate_limit.py

Rate-limit handling in WarcraftLogsClient: 429s, GraphQL rate-limit errors and
the rate_limit() helper. HTTP is mocked with respx.
"""

import httpx
import pytest
import respx

from khadbot.tools.warcraftlogs._client import (
    GRAPHQL_CLIENT_URL,
    TOKEN_URL,
    WarcraftLogsAPIError,
    WarcraftLogsClient,
    WarcraftLogsRateLimitError,
)


@pytest.fixture
def router():
    with respx.mock(assert_all_called=False) as r:
        r.post(TOKEN_URL).mock(return_value=httpx.Response(200, json={"access_token": "t", "expires_in": 3600}))
        yield r


async def _query(client_gql="query { x }"):
    async with WarcraftLogsClient("id", "secret") as wcl:
        return await wcl.query(client_gql)


class TestRateLimit:
    async def test_429_with_retry_after(self, router):
        router.post(GRAPHQL_CLIENT_URL).mock(return_value=httpx.Response(429, headers={"Retry-After": "12"}))
        with pytest.raises(WarcraftLogsRateLimitError) as exc:
            await _query()
        assert exc.value.retry_after_s == 12.0

    async def test_429_without_retry_after(self, router):
        router.post(GRAPHQL_CLIENT_URL).mock(return_value=httpx.Response(429))
        with pytest.raises(WarcraftLogsRateLimitError) as exc:
            await _query()
        assert exc.value.retry_after_s is None

    async def test_429_with_unparseable_retry_after(self, router):
        router.post(GRAPHQL_CLIENT_URL).mock(return_value=httpx.Response(429, headers={"Retry-After": "soon"}))
        with pytest.raises(WarcraftLogsRateLimitError) as exc:
            await _query()
        assert exc.value.retry_after_s is None

    async def test_graphql_rate_limit_message(self, router):
        body = {"errors": [{"message": "You have exceeded the rate limit for this hour."}]}
        router.post(GRAPHQL_CLIENT_URL).mock(return_value=httpx.Response(200, json=body))
        with pytest.raises(WarcraftLogsRateLimitError):
            await _query()

    async def test_other_http_error_is_plain_api_error(self, router):
        router.post(GRAPHQL_CLIENT_URL).mock(return_value=httpx.Response(500))
        with pytest.raises(WarcraftLogsAPIError) as exc:
            await _query()
        assert not isinstance(exc.value, WarcraftLogsRateLimitError)

    async def test_rate_limit_helper(self, router):
        data = {"rateLimitData": {"limitPerHour": 3600, "pointsSpentThisHour": 10.5, "pointsResetIn": 900}}
        router.post(GRAPHQL_CLIENT_URL).mock(return_value=httpx.Response(200, json={"data": data}))
        async with WarcraftLogsClient("id", "secret") as wcl:
            assert await wcl.rate_limit() == data["rateLimitData"]
