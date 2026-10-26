"""
db/__init__.py

Postgres support for hosted mode: connection pool, migration runner, the Postgres findings store, the event store's
per-report write lock and the read-model tables (``read_models.project_pull`` writes a pull's rows; ``queries`` reads them).

Local mode never imports this package's connection code, so it needs no database. Callers reach the
package through ``get_pool``, ``migrate``, ``PostgresFindingsStore``, ``PostgresReadModelProjector``, ``project_pull`` and ``queries``.
"""

from __future__ import annotations

from khadbot.db.connect import close_pool, get_pool, open_pool
from khadbot.db.findings_pg import PostgresFindingsStore
from khadbot.db.index import PostgresReadModelIndex
from khadbot.db.locks import PostgresReportLock
from khadbot.db.migrate import MigrationError, migrate
from khadbot.db.projector import PostgresReadModelProjector
from khadbot.db.read_models import project_pull

__all__ = [
    "MigrationError",
    "PostgresFindingsStore",
    "PostgresReadModelIndex",
    "PostgresReadModelProjector",
    "PostgresReportLock",
    "close_pool",
    "get_pool",
    "migrate",
    "open_pool",
    "project_pull",
]
