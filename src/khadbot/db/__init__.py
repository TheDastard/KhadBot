"""
db/__init__.py

Postgres support for hosted mode: connection pool, migration runner and the Postgres findings store.

Local mode never imports this package's connection code, so it needs no database. Callers reach the
package through ``get_pool``, ``migrate`` and ``PostgresFindingsStore``.
"""

from __future__ import annotations

from khadbot.db.connect import close_pool, get_pool, open_pool
from khadbot.db.findings_pg import PostgresFindingsStore
from khadbot.db.migrate import MigrationError, migrate

__all__ = ["MigrationError", "PostgresFindingsStore", "close_pool", "get_pool", "migrate", "open_pool"]
