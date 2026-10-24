"""
tests/unit/wcl/test_oauth.py

The WCL side of sign-in: PKCE, the authorize URL, the code exchange and refresh grants, and the current-user
query. HTTP is mocked with respx; every token is an obviously fake value.
"""

from __future__ import annotations

import base64
import hashlib
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
import respx

from khadbot.wcl.client import GRAPHQL_USER_URL, TOKEN_URL, WarcraftLogsClient, WarcraftLogsUnauthorizedError
from khadbot.wcl.oauth import (
    AUTHORIZE_URL,
    TokenSet,
    WclGrantRejectedError,
    WclIdentityError,
    WclOAuthError,
    authorize_url,
    current_user,
    exchange_code,
    make_pkce,
    pkce_challenge,
    refresh_tokens,
)

FAKE_ACCESS = "fake-access-token-0001"
FAKE_REFRESH = "fake-refresh-token-0001"


class TestPkce:
    def test_challenge_is_unpadded_base64url_sha256(self):
        verifier, challenge = make_pkce()
        expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        assert challenge == expected == pkce_challenge(verifier)
        assert "=" not in challenge

    def test_verifier_length_within_rfc_limits(self):
        verifier, _ = make_pkce()
        assert 43 <= len(verifier) <= 128

    def test_pairs_are_fresh(self):
        assert make_pkce()[0] != make_pkce()[0]


class TestAuthorizeUrl:
    def test_parameters(self):
        url = authorize_url(client_id="cid", redirect_uri="http://localhost:8000/v1/auth/callback", challenge="ch",
                            state="st")  # fmt: skip
        parts = urlsplit(url)
        assert f"{parts.scheme}://{parts.netloc}{parts.path}" == AUTHORIZE_URL
        assert parse_qs(parts.query) == {
            "client_id": ["cid"],
            "redirect_uri": ["http://localhost:8000/v1/auth/callback"],
            "response_type": ["code"],
            "code_challenge": ["ch"],
            "code_challenge_method": ["S256"],
            "state": ["st"],
        }

    def test_scope_only_when_given(self):
        url = authorize_url(client_id="c", redirect_uri="http://localhost/cb", challenge="x", state="s", scope="a b")
        assert parse_qs(urlsplit(url).query)["scope"] == ["a b"]


@pytest.fixture
def http():
    return httpx.AsyncClient()


class TestExchange:
    @respx.mock
    async def test_success_sends_verifier_and_secret(self, http):
        route = respx.post(TOKEN_URL).mock(
            return_value=httpx.Response(
                200, json={"access_token": FAKE_ACCESS, "refresh_token": FAKE_REFRESH, "expires_in": 3600}
            )
        )
        tokens = await exchange_code(
            http, client_id="cid", client_secret="fake-secret", code="fake-code", verifier="v" * 50,
            redirect_uri="http://localhost:8000/v1/auth/callback",
        )  # fmt: skip
        form = parse_qs(route.calls.last.request.content.decode())
        assert form["grant_type"] == ["authorization_code"]
        assert form["code_verifier"] == ["v" * 50]
        assert form["client_secret"] == ["fake-secret"]
        assert form["redirect_uri"] == ["http://localhost:8000/v1/auth/callback"]
        assert tokens.access_token.get_secret_value() == FAKE_ACCESS
        assert tokens.refresh_token is not None and tokens.refresh_token.get_secret_value() == FAKE_REFRESH
        assert tokens.expires_in == 3600

    async def test_tokens_never_in_repr(self):
        from pydantic import SecretStr

        tokens = TokenSet(access_token=SecretStr(FAKE_ACCESS), refresh_token=SecretStr(FAKE_REFRESH), expires_in=5)
        assert FAKE_ACCESS not in repr(tokens) and FAKE_REFRESH not in repr(tokens)
        assert FAKE_ACCESS not in str(tokens)

    @respx.mock
    async def test_rejected_code(self, http):
        respx.post(TOKEN_URL).mock(return_value=httpx.Response(400, json={"error": "invalid_grant"}))
        with pytest.raises(WclGrantRejectedError) as exc:
            await exchange_code(http, client_id="c", client_secret="s", code="fake-code", verifier="v",
                                redirect_uri="http://localhost/cb")  # fmt: skip
        assert exc.value.status == 400 and exc.value.error == "invalid_grant"
        assert "fake-code" not in str(exc.value)

    @respx.mock
    async def test_unrecognised_error_text_not_repeated(self, http):
        respx.post(TOKEN_URL).mock(return_value=httpx.Response(400, json={"error": "Leaky " + FAKE_ACCESS}))
        with pytest.raises(WclGrantRejectedError) as exc:
            await exchange_code(http, client_id="c", client_secret="s", code="x", verifier="v",
                                redirect_uri="http://localhost/cb")  # fmt: skip
        assert exc.value.error is None
        assert FAKE_ACCESS not in str(exc.value)

    @respx.mock
    async def test_server_error_is_not_a_grant_rejection(self, http):
        respx.post(TOKEN_URL).mock(return_value=httpx.Response(503))
        with pytest.raises(WclOAuthError) as exc:
            await exchange_code(http, client_id="c", client_secret="s", code="x", verifier="v",
                                redirect_uri="http://localhost/cb")  # fmt: skip
        assert not isinstance(exc.value, WclGrantRejectedError)

    @respx.mock
    async def test_network_error(self, http):
        respx.post(TOKEN_URL).mock(side_effect=httpx.ConnectError("boom"))
        with pytest.raises(WclOAuthError, match="ConnectError"):
            await exchange_code(http, client_id="c", client_secret="s", code="x", verifier="v",
                                redirect_uri="http://localhost/cb")  # fmt: skip

    @respx.mock
    async def test_missing_access_token(self, http):
        respx.post(TOKEN_URL).mock(return_value=httpx.Response(200, json={"token_type": "Bearer"}))
        with pytest.raises(WclOAuthError, match="no access token"):
            await exchange_code(http, client_id="c", client_secret="s", code="x", verifier="v",
                                redirect_uri="http://localhost/cb")  # fmt: skip


class TestRefresh:
    @respx.mock
    async def test_refresh_grant(self, http):
        route = respx.post(TOKEN_URL).mock(
            return_value=httpx.Response(200, json={"access_token": "fake-access-2", "expires_in": 60})
        )
        tokens = await refresh_tokens(http, client_id="c", client_secret="s", refresh_token=FAKE_REFRESH)
        form = parse_qs(route.calls.last.request.content.decode())
        assert form["grant_type"] == ["refresh_token"] and form["refresh_token"] == [FAKE_REFRESH]
        assert tokens.access_token.get_secret_value() == "fake-access-2"
        assert tokens.refresh_token is None

    @respx.mock
    async def test_revoked_refresh_token(self, http):
        respx.post(TOKEN_URL).mock(return_value=httpx.Response(401, json={"error": "invalid_client"}))
        with pytest.raises(WclGrantRejectedError) as exc:
            await refresh_tokens(http, client_id="c", client_secret="s", refresh_token=FAKE_REFRESH)
        assert FAKE_REFRESH not in str(exc.value)


class TestCurrentUser:
    @respx.mock
    async def test_reads_user_on_user_endpoint(self):
        route = respx.post(GRAPHQL_USER_URL).mock(
            return_value=httpx.Response(200, json={"data": {"userData": {"currentUser": {"id": 42, "name": "Khad"}}}})
        )
        async with WarcraftLogsClient("cid", "secret") as wcl:
            user = await current_user(wcl, FAKE_ACCESS)
        assert (user.id, user.name) == (42, "Khad")
        assert route.calls.last.request.headers["Authorization"] == f"Bearer {FAKE_ACCESS}"

    @respx.mock
    async def test_no_user(self):
        respx.post(GRAPHQL_USER_URL).mock(
            return_value=httpx.Response(200, json={"data": {"userData": {"currentUser": None}}})
        )
        async with WarcraftLogsClient("cid", "secret") as wcl:
            with pytest.raises(WclIdentityError):
                await current_user(wcl, FAKE_ACCESS)

    @respx.mock
    async def test_rejected_token(self):
        respx.post(GRAPHQL_USER_URL).mock(return_value=httpx.Response(401))
        async with WarcraftLogsClient("cid", "secret") as wcl:
            with pytest.raises(WarcraftLogsUnauthorizedError):
                await current_user(wcl, FAKE_ACCESS)
