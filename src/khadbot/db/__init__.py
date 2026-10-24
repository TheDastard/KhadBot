"""
db/__init__.py

Postgres support for hosted mode: connection pool, migration runner, the Postgres findings store and the read-model
tables (``read_models.project_pull`` writes a pull's rows; ``queries`` reads them).

Local mode never imports this package's connection code, so it needs no database. Callers reach the
package through ``get_pool``, ``migrate``, ``PostgresFindingsStore``, ``PostgresReadModelProjector``, ``project_pull`` and ``queries``.
"""

from __future__ import annotations

from khadbot.db.connect import close_pool, get_pool, open_pool
from khadbot.db.findings_pg import PostgresFindingsStore
from khadbot.db.migrate import MigrationError, migrate
from khadbot.db.projector import PostgresReadModelProjector
from khadbot.db.read_models import project_pull

__all__ = [
    "MigrationError",
    "PostgresFindingsStore",
    "PostgresReadModelProjector",
    "close_pool",
    "get_pool",
    "migrate",
    "open_pool",
    "project_pull",
]
