"""
tests/unit/db/test_connect.py

Pool construction and the mode switch in default_findings_store. No Postgres: ConnectionPool is replaced.
"""

from __future__ import annotations

import pytest

from khadbot.config import DatabaseConfig, DeploymentMode, reset_config
from khadbot.db import PostgresFindingsStore, connect
from khadbot.encounter import findings
from khadbot.encounter.findings import SqliteFindingsStore, default_findings_store


class FakePool:
    instances: list[FakePool] = []

    def __init__(self, conninfo, **kwargs):
        self.conninfo = conninfo
        self.kwargs = kwargs
        self.opened = None
        self.closed = False
        FakePool.instances.append(self)

    def open(self, **kwargs):
        self.opened = kwargs

    def close(self):
        self.closed = True


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for var in ("KHADBOT_MODE", "DATABASE_URL", "KHADBOT_DB_POOL_MIN", "KHADBOT_DB_POOL_MAX"):
        monkeypatch.delenv(var, raising=False)
    FakePool.instances = []
    monkeypatch.setattr(connect, "ConnectionPool", FakePool)
    monkeypatch.setattr(connect, "_pool", None)
    reset_config()
    yield
    reset_config()


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def test_defaults_are_local_with_no_database():
    cfg = DatabaseConfig()
    assert cfg.mode is DeploymentMode.LOCAL
    assert cfg.hosted is False
    assert cfg.database_url == ""


def test_hosted_mode_and_pool_sizes_come_from_env(monkeypatch):
    monkeypatch.setenv("KHADBOT_MODE", "hosted")
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:fake@db/x")
    monkeypatch.setenv("KHADBOT_DB_POOL_MAX", "4")
    cfg = DatabaseConfig()
    assert cfg.hosted is True
    assert (cfg.database_url, cfg.pool_min_size, cfg.pool_max_size) == ("postgresql://u:fake@db/x", 1, 4)


def test_unknown_mode_is_an_error(monkeypatch):
    monkeypatch.setenv("KHADBOT_MODE", "cloud")
    with pytest.raises(ValueError, match="cloud"):
        DatabaseConfig()


# ---------------------------------------------------------------------------
# Pool
# ---------------------------------------------------------------------------


def test_open_pool_requires_a_url():
    with pytest.raises(OSError, match="DATABASE_URL"):
        connect.open_pool()
    assert FakePool.instances == []


def test_open_pool_uses_env_url_and_waits(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:fake@db/x")
    pool = connect.open_pool()
    assert pool.conninfo == "postgresql://u:fake@db/x"
    assert pool.kwargs["open"] is False  # opened explicitly, so no implicit-open deprecation
    assert pool.opened["wait"] is True


def test_get_pool_is_shared_until_closed(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:fake@db/x")
    first = connect.get_pool()
    assert connect.get_pool() is first
    connect.close_pool()
    assert first.closed is True
    assert connect.get_pool() is not first


# ---------------------------------------------------------------------------
# Mode switch
# ---------------------------------------------------------------------------


def test_default_store_is_sqlite_in_local_mode(monkeypatch, tmp_path):
    monkeypatch.setenv("KHADBOT_DATA_DIR", str(tmp_path))
    store = default_findings_store()
    assert isinstance(store, SqliteFindingsStore)
    assert store.db_path == tmp_path / "khadbot.sqlite"
    assert FakePool.instances == []  # local mode never touches the database


def test_default_store_is_postgres_in_hosted_mode(monkeypatch):
    monkeypatch.setenv("KHADBOT_MODE", "hosted")
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:fake@db/x")
    store = findings.default_findings_store()
    assert isinstance(store, PostgresFindingsStore)
    assert len(FakePool.instances) == 1
