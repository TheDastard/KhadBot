"""
db/connect.py

Connection pool for hosted mode, built from ``DATABASE_URL`` (see ``DatabaseConfig``).

``open_pool`` builds a pool; ``get_pool`` returns the process-wide one, opening it on first use.
Nothing here runs in local mode.
"""

from __future__ import annotations

import threading
from typing import Any

from psycopg_pool import ConnectionPool

from khadbot.config import DatabaseConfig

# ---------------------------------------------------------------------------
# Pool
# ---------------------------------------------------------------------------

_pool: ConnectionPool | None = None
_lock = threading.Lock()


def open_pool(
    url: str | None = None,
    *,
    min_size: int | None = None,
    max_size: int | None = None,
    kwargs: dict[str, Any] | None = None,
) -> ConnectionPool:
    """
    Open a new pool and wait for its first connection.

    Args:
        url: A libpq connection string or URL. Defaults to ``DATABASE_URL``.
        min_size: Connections kept open. Defaults to ``DatabaseConfig.pool_min_size``.
        max_size: Connection ceiling. Defaults to ``DatabaseConfig.pool_max_size``.
        kwargs: Extra arguments for each ``psycopg.connect`` call (tests use it to set a search path).

    Raises:
        OSError: ``url`` is not given and ``DATABASE_URL`` is not set.
        psycopg_pool.PoolTimeout: No connection could be made in time.
    """
    cfg = DatabaseConfig()
    conninfo = url or cfg.database_url
    if not conninfo:
        raise OSError("Required environment variable 'DATABASE_URL' is not set.")
    pool = ConnectionPool(
        conninfo,
        min_size=cfg.pool_min_size if min_size is None else min_size,
        max_size=cfg.pool_max_size if max_size is None else max_size,
        kwargs=kwargs,
        open=False,
    )
    pool.open(wait=True, timeout=30)
    return pool


def get_pool() -> ConnectionPool:
    """The process-wide pool, opened from ``DATABASE_URL`` on first use."""
    global _pool
    with _lock:
        if _pool is None:
            _pool = open_pool()
        return _pool


def close_pool() -> None:
    """Close the process-wide pool if it is open."""
    global _pool
    with _lock:
        pool, _pool = _pool, None
    if pool is not None:
        pool.close()
