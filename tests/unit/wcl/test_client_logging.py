"""
tests/unit/wcl/test_client_logging.py

The ``wcl.request`` records WarcraftLogsClient writes: one per HTTP request, with the operation name, status,
duration and attempt, DEBUG on success and WARNING on failure, and never the token, the Authorization header or the
query variables. HTTP is mocked with respx; every credential is an obviously fake value.
"""

from __future__ import annotations

import logging

import httpx
import pytest
import respx

from khadbot.wcl.client import (
    GRAPHQL_CLIENT_URL,
    GRAPHQL_USER_URL,
    TOKEN_URL,
    WarcraftLogsAPIError,
    WarcraftLogsAuthError,
    WarcraftLogsClient,
    WarcraftLogsNetworkError,
    operation_name,
)

FAKE_SECRET = "fake-client-secret-0001"
FAKE_APP_TOKEN = "fake-app-token-0001"
FAKE_USER_TOKEN = "fake-user-token-0001"
PRIVATE_CODE = "PrivCode9xYz"
QUERY = "query GetReportMeta($code: String!) { reportData { report(code: $code) { title } } }"


@pytest.fixture
def router():
    with respx.mock(assert_all_called=False) as r:
        r.post(TOKEN_URL).mock(
            return_value=httpx.Response(200, json={"access_token": FAKE_APP_TOKEN, "expires_in": 3600})
        )
        yield r


@pytest.fixture
def records(caplog):
    caplog.set_level(logging.DEBUG, logger="khadbot.wcl.client")
    return lambda: [r for r in caplog.records if getattr(r, "event", None) == "wcl.request"]


async def _query(**kwargs):
    async with WarcraftLogsClient("fake-client-id", FAKE_SECRET) as wcl:
        return await wcl.query(QUERY, {"code": PRIVATE_CODE}, **kwargs)


@pytest.mark.parametrize(
    ("gql", "expected"),
    [
        (QUERY, "GetReportMeta"),
        ("\n  query GetEventsPage(\n  $code: String!) { x }", "GetEventsPage"),
        ("query { rateLimitData { limitPerHour } }", "rateLimitData"),
        ("{ userData { currentUser { id } } }", "userData"),
        ("", "anonymous"),
    ],
)
def test_the_operation_label(gql, expected):
    assert operation_name(gql) == expected


async def test_a_successful_call_logs_debug_records_for_the_token_and_the_query(router, records):
    router.post(GRAPHQL_CLIENT_URL).mock(return_value=httpx.Response(200, json={"data": {"reportData": {}}}))
    await _query()
    token, call = records()
    assert (token.op, token.status, token.attempt, token.levelno) == ("oauth_token", 200, 1, logging.DEBUG)
    assert (call.op, call.status, call.attempt, call.levelno) == ("GetReportMeta", 200, 1, logging.DEBUG)
    assert isinstance(call.duration_ms, int) and call.duration_ms >= 0
    assert call.getMessage().startswith("WCL GetReportMeta 200 in ")


async def test_the_caller_attempt_is_recorded(router, records):
    router.post(GRAPHQL_USER_URL).mock(return_value=httpx.Response(200, json={"data": {}}))
    await _query(user_token=FAKE_USER_TOKEN, attempt=3)
    (call,) = records()  # a user token needs no app token request
    assert call.attempt == 3


@pytest.mark.parametrize(
    ("response", "error"),
    [
        (httpx.Response(500), WarcraftLogsAPIError),
        (httpx.Response(200, json={"errors": [{"message": "Unknown report"}]}), WarcraftLogsAPIError),
    ],
)
async def test_a_failed_call_logs_a_warning_with_its_status(router, records, response, error):
    router.post(GRAPHQL_CLIENT_URL).mock(return_value=response)
    with pytest.raises(error):
        await _query()
    call = records()[-1]
    assert (call.op, call.status, call.levelno) == ("GetReportMeta", response.status_code, logging.WARNING)


async def test_a_timeout_logs_a_warning_without_a_status(router, records):
    router.post(GRAPHQL_CLIENT_URL).mock(side_effect=httpx.ReadTimeout("too slow"))
    with pytest.raises(WarcraftLogsNetworkError):
        await _query()
    call = records()[-1]
    assert call.levelno == logging.WARNING
    assert not hasattr(call, "status")
    assert "ReadTimeout" in call.getMessage()


async def test_a_failed_token_request_logs_a_warning(records):
    with respx.mock() as r:
        r.post(TOKEN_URL).mock(return_value=httpx.Response(401))
        with pytest.raises(WarcraftLogsAuthError):
            await _query()
    (token,) = records()
    assert (token.op, token.status, token.levelno) == ("oauth_token", 401, logging.WARNING)


async def test_no_record_carries_a_credential_or_the_variables(router, records, caplog):
    router.post(GRAPHQL_CLIENT_URL).mock(return_value=httpx.Response(200, json={"data": {}}))
    router.post(GRAPHQL_USER_URL).mock(return_value=httpx.Response(500))
    await _query()
    with pytest.raises(WarcraftLogsAPIError):
        await _query(user_token=FAKE_USER_TOKEN)
    assert len(records()) == 3
    for record in caplog.records:
        text = record.getMessage() + " " + repr(vars(record))
        for secret in (FAKE_SECRET, FAKE_APP_TOKEN, FAKE_USER_TOKEN, "Bearer", "Authorization", PRIVATE_CODE):
            assert secret not in text, (secret, record.getMessage())
