"""
tests/unit/auth/test_tokens.py

Stored WCL tokens: encryption at rest, key rotation with MultiFernet, refresh on expiry, the per-user refresh
lock, and what happens when WCL refuses or can't be reached. WCL is faked with respx; keys are generated here.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
from cryptography.fernet import Fernet
from helpers.auth.fake_wcl import FAKE_CLIENT_ID, FAKE_CLIENT_SECRET
from pydantic import SecretStr

from khadbot.auth.errors import WclSignInRequiredError, WclUnavailableError
from khadbot.auth.store import MemoryAuthStore
from khadbot.auth.tokens import TokenCipher, TokenDecryptError, WclTokens
from khadbot.errors import ConfigurationError
from khadbot.wcl.oauth import TokenSet


def tokens_for(store, clock, *keys):
    return WclTokens(store, TokenCipher(list(keys)), client_id=FAKE_CLIENT_ID, client_secret=FAKE_CLIENT_SECRET,
                     clock=clock)  # fmt: skip


def token_set(n=1, expires_in=3600):
    return TokenSet(SecretStr(f"fake-access-{n}"), SecretStr(f"fake-refresh-{n}"), expires_in)


@pytest.fixture
def key() -> str:
    return Fernet.generate_key().decode()


class TestCipher:
    def test_round_trip_and_ciphertext_differs(self, key):
        cipher = TokenCipher([key])
        sealed = cipher.encrypt("fake-refresh-1")
        assert "fake-refresh-1" not in sealed
        assert cipher.decrypt(sealed) == "fake-refresh-1"

    def test_no_keys(self):
        with pytest.raises(ConfigurationError, match="KHADBOT_TOKEN_KEYS"):
            TokenCipher([])

    def test_bad_key(self):
        with pytest.raises(ConfigurationError, match="not a Fernet key"):
            TokenCipher(["not-a-key"])

    def test_wrong_key(self, key):
        sealed = TokenCipher([key]).encrypt("x")
        with pytest.raises(TokenDecryptError):
            TokenCipher([Fernet.generate_key().decode()]).decrypt(sealed)

    def test_repr_hides_keys(self, key):
        assert key not in repr(TokenCipher([key]))

    def test_rotation(self, key):
        old = TokenCipher([key])
        new_key = Fernet.generate_key().decode()
        sealed = old.encrypt("fake-refresh-1")
        both = TokenCipher([new_key, key])
        assert both.decrypt(sealed) == "fake-refresh-1"  # old ciphertext still reads during rotation
        rotated = both.rotate(sealed)
        assert TokenCipher([new_key]).decrypt(rotated) == "fake-refresh-1"  # and the old key can then go
        with pytest.raises(TokenDecryptError):
            old.decrypt(rotated)


class TestStoredTokens:
    async def test_stored_encrypted(self, clock, key):
        store = MemoryAuthStore()
        await tokens_for(store, clock, key).save("u1", token_set())
        stored = store.tokens["u1"]
        assert "fake-access-1" not in stored.access_enc and "fake-refresh-1" not in (stored.refresh_enc or "")
        assert "fake-access-1" not in repr(stored) and "fake-refresh-1" not in repr(stored)
        assert stored.access_expires_at == clock.now + timedelta(hours=1)

    async def test_fresh_token_needs_no_wcl(self, clock, key, wcl):
        store = MemoryAuthStore()
        tokens = tokens_for(store, clock, key)
        await tokens.save("u1", token_set())
        assert await tokens.access_token("u1") == "fake-access-1"
        assert not wcl.token_requests

    async def test_unknown_user(self, clock, key):
        with pytest.raises(WclSignInRequiredError):
            await tokens_for(MemoryAuthStore(), clock, key).access_token("nobody")

    async def test_refresh_on_expiry(self, clock, key, wcl):
        store = MemoryAuthStore()
        tokens = tokens_for(store, clock, key)
        await tokens.save("u1", token_set(n=0))
        clock.advance(minutes=59, seconds=30)  # inside the refresh margin
        new = await tokens.access_token("u1")
        assert new == "fake-access-1"
        (request,) = wcl.token_requests
        assert request["grant_type"] == ["refresh_token"] and request["refresh_token"] == ["fake-refresh-0"]
        assert await tokens.access_token("u1") == "fake-access-1"  # stored: no second refresh
        assert len(wcl.token_requests) == 1
        assert TokenCipher([key]).decrypt(store.tokens["u1"].refresh_enc or "") == "fake-refresh-1"

    async def test_refresh_without_a_new_refresh_token_keeps_the_old_one(self, clock, key, wcl):
        import httpx

        from khadbot.wcl.client import TOKEN_URL

        store = MemoryAuthStore()
        tokens = tokens_for(store, clock, key)
        await tokens.save("u1", token_set(n=0))
        wcl.router.post(TOKEN_URL).mock(
            return_value=httpx.Response(200, json={"access_token": "fake-access-9", "expires_in": 3600})
        )
        clock.advance(hours=2)
        assert await tokens.access_token("u1") == "fake-access-9"
        assert TokenCipher([key]).decrypt(store.tokens["u1"].refresh_enc or "") == "fake-refresh-0"

    async def test_force_refresh(self, clock, key, wcl):
        store = MemoryAuthStore()
        tokens = tokens_for(store, clock, key)
        await tokens.save("u1", token_set(n=0))
        assert await tokens.access_token("u1", force_refresh=True) == "fake-access-1"

    async def test_refused_refresh_deletes_tokens(self, clock, key, wcl):
        store = MemoryAuthStore()
        tokens = tokens_for(store, clock, key)
        await tokens.save("u1", token_set(n=0))
        wcl.refresh_status = 400
        clock.advance(hours=2)
        with pytest.raises(WclSignInRequiredError) as exc:
            await tokens.access_token("u1")
        assert "u1" not in store.tokens
        assert "fake-refresh-0" not in str(exc.value)

    async def test_wcl_down_keeps_tokens(self, clock, key, wcl):
        store = MemoryAuthStore()
        tokens = tokens_for(store, clock, key)
        await tokens.save("u1", token_set(n=0))
        wcl.refresh_status = 503
        clock.advance(hours=2)
        with pytest.raises(WclUnavailableError):
            await tokens.access_token("u1")
        assert "u1" in store.tokens

    async def test_no_refresh_token(self, clock, key, wcl):
        store = MemoryAuthStore()
        tokens = tokens_for(store, clock, key)
        await tokens.save("u1", TokenSet(SecretStr("fake-access-0"), None, 60))
        clock.advance(hours=1)
        with pytest.raises(WclSignInRequiredError):
            await tokens.access_token("u1")

    async def test_undecryptable_tokens_need_a_new_sign_in(self, clock, key):
        store = MemoryAuthStore()
        await tokens_for(store, clock, key).save("u1", token_set())
        with pytest.raises(WclSignInRequiredError):
            await tokens_for(store, clock, Fernet.generate_key().decode()).access_token("u1")

    async def test_per_user_lock_refreshes_once(self, clock, key, wcl):
        """Many tasks find the token expired at once: one refresh, and all get the new token."""
        store = MemoryAuthStore()
        tokens = tokens_for(store, clock, key)
        await tokens.save("u1", token_set(n=0))
        await tokens.save("u2", token_set(n=100))
        clock.advance(hours=2)
        wcl.refresh_delay_s = 0.05
        results = await asyncio.gather(*(tokens.access_token("u1") for _ in range(5)), tokens.access_token("u2"))
        assert len(set(results[:5])) == 1
        refreshes = [r["refresh_token"][0] for r in wcl.token_requests]
        assert sorted(refreshes) == ["fake-refresh-0", "fake-refresh-100"]  # one per user

    async def test_two_services_share_the_store_lock(self, clock, key, wcl):
        """Two workers (two ``WclTokens`` over one store) still refresh once."""
        store = MemoryAuthStore()
        first, second = tokens_for(store, clock, key), tokens_for(store, clock, key)
        await first.save("u1", token_set(n=0))
        clock.advance(hours=2)
        wcl.refresh_delay_s = 0.05
        a, b = await asyncio.gather(first.access_token("u1"), second.access_token("u1"))
        assert a == b and len(wcl.token_requests) == 1

    async def test_rotate_keys(self, clock, key):
        store = MemoryAuthStore()
        await tokens_for(store, clock, key).save("u1", token_set())
        new_key = Fernet.generate_key().decode()
        assert await tokens_for(store, clock, new_key, key).rotate_keys() == 1
        assert await tokens_for(store, clock, new_key).access_token("u1") == "fake-access-1"
