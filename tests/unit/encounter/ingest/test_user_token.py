"""
tests/unit/encounter/ingest/test_user_token.py

Ingest on a user's WCL token. The real WarcraftLogsClient runs against respx routes, so the tests see the
endpoint and bearer header of every request. Token values are obviously fake.
"""

from __future__ import annotations

import logging

import httpx
import pytest
import respx

from khadbot.encounter.ingest import ingest_report
from khadbot.encounter.ingest.fetch import FETCHED_WITH_KEY, RawPageCache, UserTokenRejectedError, query_wcl
from khadbot.encounter.ingest.wcl_queries import GET_REPORT_META
from khadbot.encounter.store import ParquetEventStore
from khadbot.storage import LocalBlobStore
from khadbot.wcl.client import (
    GRAPHQL_CLIENT_URL,
    GRAPHQL_USER_URL,
    RATE_LIMIT_QUERY,
    TOKEN_URL,
    WarcraftLogsClient,
)

CODE = "SYNTH01"
USER_TOKEN = "fake-user-token-AAAA1111"
REFRESHED_TOKEN = "fake-user-token-BBBB2222"
APP_TOKEN = "fake-app-token-CCCC3333"
CLIENT_ID = "fake-client-id"
CLIENT_SECRET = "fake-client-secret-DDDD4444"
SECRETS = (USER_TOKEN, REFRESHED_TOKEN, APP_TOKEN, CLIENT_SECRET)


def graphql_handler(meta: dict, rejected: set[str] | None = None, points: list[float] | None = None):
    """A respx side effect: 401 for tokens in ``rejected``, else metadata, rate limit or an empty events page."""
    seen_points = iter(points or [0.0, 0.0])

    def handler(request: httpx.Request) -> httpx.Response:
        bearer = request.headers["Authorization"].removeprefix("Bearer ")
        if bearer in (rejected or set()):
            return httpx.Response(401, json={"error": "invalid_token"})
        gql = request.content.decode()
        if "rateLimitData" in gql:
            return httpx.Response(200, json={"data": {"rateLimitData": {"pointsSpentThisHour": next(seen_points)}}})
        if "masterData" in gql:
            return httpx.Response(200, json={"data": {"reportData": {"report": meta}}})
        return httpx.Response(
            200, json={"data": {"reportData": {"report": {"events": {"data": [], "nextPageTimestamp": None}}}}}
        )

    return handler


@pytest.fixture
def meta(synth_cache) -> dict:
    return synth_cache.report_meta(CODE)


@pytest.fixture
def router():
    with respx.mock(assert_all_called=False) as r:
        r.post(TOKEN_URL).mock(return_value=httpx.Response(200, json={"access_token": APP_TOKEN, "expires_in": 3600}))
        yield r


@pytest.fixture
def stores(tmp_path):
    blobs = LocalBlobStore(tmp_path / "data")  # synth_cache already fills tmp_path/raw
    return ParquetEventStore(blobs), RawPageCache(blobs)


def provider(*tokens: str):
    """An async token provider handing out ``tokens`` in turn (the last repeats); counts its calls."""
    calls = {"n": 0}

    async def get() -> str:
        token = tokens[min(calls["n"], len(tokens) - 1)]
        calls["n"] += 1
        return token

    get.calls = calls  # type: ignore[attr-defined]
    return get


async def run_ingest(stores, token_provider, **kwargs):
    store, cache = stores
    async with WarcraftLogsClient(CLIENT_ID, CLIENT_SECRET) as client:
        return await ingest_report(
            CODE, [1], store=store, cache=cache, client=client, token_provider=token_provider, **kwargs
        )


class TestUserTokenIngest:
    async def test_every_request_goes_to_the_user_endpoint_with_the_bearer(self, router, meta, stores):
        user_route = router.post(GRAPHQL_USER_URL).mock(side_effect=graphql_handler(meta))
        app_route = router.post(GRAPHQL_CLIENT_URL).mock(side_effect=graphql_handler(meta))

        result = await run_ingest(stores, provider(USER_TOKEN))

        assert result.pulls == [f"{CODE}:1"] and not result.failed
        assert app_route.call_count == 0
        # report metadata + one rate-limit read each side + one request per spec
        assert user_route.call_count >= 4
        assert {c.request.headers["Authorization"] for c in user_route.calls} == {f"Bearer {USER_TOKEN}"}
        assert not [c for c in router.calls if c.request.url == TOKEN_URL]  # the app token was never fetched

    async def test_points_spent_is_read_from_the_user_budget(self, router, meta, stores):
        router.post(GRAPHQL_USER_URL).mock(side_effect=graphql_handler(meta, points=[100.0, 160.0]))
        result = await run_ingest(stores, provider(USER_TOKEN))
        assert result.points_spent == 60.0

    async def test_report_meta_records_a_user_token_but_not_which_user(self, router, meta, stores):
        router.post(GRAPHQL_USER_URL).mock(side_effect=graphql_handler(meta))
        await run_ingest(stores, provider(USER_TOKEN))
        _, cache = stores
        assert cache.fetched_with(CODE) == "user"
        stored = cache.report_meta(CODE)
        assert stored is not None and stored[FETCHED_WITH_KEY] == "user"
        assert {k: v for k, v in stored.items() if k != FETCHED_WITH_KEY} == meta

    async def test_app_token_ingest_records_app(self, router, meta, stores):
        user_route = router.post(GRAPHQL_USER_URL).mock(side_effect=graphql_handler(meta))
        app_route = router.post(GRAPHQL_CLIENT_URL).mock(side_effect=graphql_handler(meta))
        await run_ingest(stores, None)
        _, cache = stores
        assert cache.fetched_with(CODE) == "app"
        assert user_route.call_count == 0
        assert {c.request.headers["Authorization"] for c in app_route.calls} == {f"Bearer {APP_TOKEN}"}

    async def test_metadata_without_the_field_loads_as_unrecorded(self, meta, stores):
        _, cache = stores
        cache.write_report_meta(CODE, meta)
        assert cache.fetched_with(CODE) is None
        cache.write_report_meta(CODE, {**meta, FETCHED_WITH_KEY: "bogus"})
        assert cache.fetched_with(CODE) is None

    async def test_cached_metadata_is_not_rewritten_by_a_later_ingest(self, router, meta, stores):
        _, cache = stores
        cache.write_report_meta(CODE, meta)  # a pre-existing report.json without the field
        router.post(GRAPHQL_USER_URL).mock(side_effect=graphql_handler(meta))
        await run_ingest(stores, provider(USER_TOKEN))
        assert cache.fetched_with(CODE) is None
        assert cache.report_meta(CODE) == meta

    async def test_refresh_meta_uses_the_user_token(self, router, meta, stores):
        user_route = router.post(GRAPHQL_USER_URL).mock(side_effect=graphql_handler(meta))
        router.post(GRAPHQL_CLIENT_URL).mock(side_effect=graphql_handler(meta))
        await run_ingest(stores, provider(USER_TOKEN))
        user_route.calls.reset()
        await run_ingest(stores, provider(USER_TOKEN), refresh_meta=True)
        assert user_route.call_count > 0


class TestUnauthorizedRetry:
    async def test_expired_token_is_refreshed_once_and_the_request_retried(self, router, meta, stores):
        route = router.post(GRAPHQL_USER_URL).mock(side_effect=graphql_handler(meta, rejected={USER_TOKEN}))
        token_provider = provider(USER_TOKEN, REFRESHED_TOKEN)

        result = await run_ingest(stores, token_provider)

        assert result.pulls == [f"{CODE}:1"] and not result.failed
        bearers = [c.request.headers["Authorization"] for c in route.calls]
        assert bearers[0] == f"Bearer {USER_TOKEN}"  # the rejected try (the first rate-limit read)
        assert f"Bearer {REFRESHED_TOKEN}" in bearers
        assert token_provider.calls["n"] >= 2

    async def test_second_rejection_raises_a_typed_error(self, router, meta):
        route = router.post(GRAPHQL_USER_URL).mock(
            side_effect=graphql_handler(meta, rejected={USER_TOKEN, REFRESHED_TOKEN})
        )
        token_provider = provider(USER_TOKEN, REFRESHED_TOKEN)
        async with WarcraftLogsClient(CLIENT_ID, CLIENT_SECRET) as client:
            with pytest.raises(UserTokenRejectedError):
                await query_wcl(client, GET_REPORT_META, {"code": CODE}, token_provider)
        assert route.call_count == 2  # one try, one retry, no more
        assert token_provider.calls["n"] == 2

    async def test_rejected_report_metadata_fails_the_ingest(self, router, meta, stores):
        router.post(GRAPHQL_USER_URL).mock(side_effect=graphql_handler(meta, rejected={USER_TOKEN, REFRESHED_TOKEN}))
        with pytest.raises(UserTokenRejectedError):
            await run_ingest(stores, provider(USER_TOKEN, REFRESHED_TOKEN))

    async def test_app_token_unauthorized_is_not_retried(self, router, meta):
        route = router.post(GRAPHQL_CLIENT_URL).mock(return_value=httpx.Response(401))
        async with WarcraftLogsClient(CLIENT_ID, CLIENT_SECRET) as client:
            with pytest.raises(Exception, match="401"):
                await query_wcl(client, RATE_LIMIT_QUERY, {})
        assert route.call_count == 1


class TestTokenNeverLeaks:
    async def test_not_in_logs_or_the_blob_store(self, router, meta, stores, tmp_path, caplog):
        router.post(GRAPHQL_USER_URL).mock(side_effect=graphql_handler(meta, rejected={USER_TOKEN}))
        caplog.set_level(logging.DEBUG)

        await run_ingest(stores, provider(USER_TOKEN, REFRESHED_TOKEN))

        logged = "\n".join(r.getMessage() + str(r.exc_text) for r in caplog.records)
        assert logged  # the capture works
        stored = b"".join(p.read_bytes() for p in (tmp_path / "data").rglob("*") if p.is_file())
        assert stored
        for secret in SECRETS:
            assert secret not in logged
            assert secret.encode() not in stored

    async def test_not_in_logs_when_the_token_is_rejected_twice(self, router, meta, caplog):
        router.post(GRAPHQL_USER_URL).mock(side_effect=graphql_handler(meta, rejected={USER_TOKEN, REFRESHED_TOKEN}))
        caplog.set_level(logging.DEBUG)
        async with WarcraftLogsClient(CLIENT_ID, CLIENT_SECRET) as client:
            with pytest.raises(UserTokenRejectedError) as exc:
                await query_wcl(client, GET_REPORT_META, {"code": CODE}, provider(USER_TOKEN, REFRESHED_TOKEN))
        text = "\n".join(r.getMessage() for r in caplog.records) + str(exc.value) + repr(exc.value.__cause__)
        assert USER_TOKEN not in text and REFRESHED_TOKEN not in text
