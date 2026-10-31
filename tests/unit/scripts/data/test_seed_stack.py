"""
tests/unit/scripts/data/test_seed_stack.py

The seed's smoke-user provisioning runs inside the seed's own event loop, so it must be awaitable (not call
``asyncio.run``), do nothing with test sessions off, and close the pool it opened. The Postgres store is replaced by
the in-memory one.
"""

from __future__ import annotations

import pytest
from helpers.scripts import load_script

from khadbot.auth.store import MemoryAuthStore

seed_stack = load_script("data", "seed_stack")


@pytest.fixture
def stores(monkeypatch):
    store, closed = MemoryAuthStore(), []
    monkeypatch.setattr("khadbot.db.get_pool", lambda: object())
    monkeypatch.setattr("khadbot.db.close_pool", lambda: closed.append(True))
    monkeypatch.setattr("khadbot.auth.pg.PostgresAuthStore", lambda pool: store)
    return store, closed


async def test_provisions_the_smoke_user_inside_a_running_loop(monkeypatch, stores):
    store, closed = stores
    monkeypatch.setenv("KHADBOT_TEST_SESSIONS", "1")
    await seed_stack.provision_smoke_session(["AbC123"])
    (user,) = store.users.values()
    assert user.name == "Test smoke" and store.get_access(user.id, "AbC123").readable
    assert closed == [True]


async def test_does_nothing_with_test_sessions_off(monkeypatch, stores, capsys):
    store, closed = stores
    monkeypatch.delenv("KHADBOT_TEST_SESSIONS", raising=False)
    await seed_stack.provision_smoke_session(["AbC123"])
    assert store.users == {} and closed == []
    assert "off" in capsys.readouterr().out
