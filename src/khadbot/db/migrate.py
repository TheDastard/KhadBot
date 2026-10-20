"""
db/migrate.py

Migration runner: applies the numbered ``NNNN_<name>.sql`` files in ``db/migrations/`` in order.

Each file runs in its own transaction together with its ``schema_migrations`` row, so a failed file leaves no
trace. A session-level advisory lock serialises runners, so two processes migrating at once apply each file once.
An applied file whose content later changes is an error: schema changes go in a new file.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import LiteralString, cast

from psycopg_pool import ConnectionPool

MIGRATIONS_DIR = Path(__file__).parent / "migrations"

# Arbitrary constant naming this lock; every runner takes the same one.
MIGRATION_LOCK_KEY = 0x4B424D47

_FILE_NAME = re.compile(r"^(\d{4})_[a-z0-9_]+\.sql$")

_CREATE_TABLE: LiteralString = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version    integer PRIMARY KEY,
    name       text NOT NULL,
    checksum   text NOT NULL,
    applied_at timestamptz NOT NULL DEFAULT now()
)
"""


class MigrationError(Exception):
    """The migration files or the recorded history are inconsistent."""


# ---------------------------------------------------------------------------
# Files
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Migration:
    """One migration file."""

    version: int
    name: str
    sql: str
    checksum: str


def load_migrations(directory: Path | None = None) -> list[Migration]:
    """
    Read the migration files, ordered by number.

    Raises:
        MigrationError: A ``.sql`` file is misnamed, two files share a number, or numbers skip one.
    """
    directory = directory or MIGRATIONS_DIR
    found: list[Migration] = []
    for path in sorted(directory.glob("*.sql")):
        match = _FILE_NAME.match(path.name)
        if match is None:
            raise MigrationError(f"Migration file '{path.name}' must be named NNNN_<lowercase_name>.sql.")
        # Line endings differ between a Windows checkout and CI; the checksum must not.
        sql = path.read_bytes().decode("utf-8").replace("\r\n", "\n")
        found.append(Migration(int(match.group(1)), path.stem, sql, hashlib.sha256(sql.encode()).hexdigest()))
    for expected, migration in enumerate(found, start=1):
        if migration.version != expected:
            raise MigrationError(
                f"Migration numbers must run 1, 2, 3, ... without repeats or gaps; '{migration.name}' "
                f"is number {migration.version} where {expected} was expected."
            )
    return found


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def migrate(pool: ConnectionPool, directory: Path | None = None) -> list[str]:
    """
    Apply every migration not yet recorded in ``schema_migrations``.

    Args:
        pool: Pool for the target database.
        directory: Migration files. Defaults to the package's own ``migrations/``.

    Returns:
        The names of the migrations applied by this call, in order (empty when up to date).

    Raises:
        MigrationError: The files are inconsistent, or an applied file has changed since it ran.
    """
    migrations = load_migrations(directory)
    applied_names: list[str] = []
    with pool.connection() as conn:
        conn.execute("SELECT pg_advisory_lock(%s)", (MIGRATION_LOCK_KEY,))
        try:
            conn.execute(_CREATE_TABLE)
            conn.commit()
            recorded = {
                int(version): checksum
                for version, checksum in conn.execute("SELECT version, checksum FROM schema_migrations").fetchall()
            }
            conn.commit()
            for migration in migrations:
                if migration.version in recorded:
                    if recorded[migration.version] != migration.checksum:
                        raise MigrationError(
                            f"Migration '{migration.name}' was applied with different content; "
                            "put the change in a new migration file."
                        )
                    continue
                conn.execute(cast(LiteralString, migration.sql))
                conn.execute(
                    "INSERT INTO schema_migrations (version, name, checksum) VALUES (%s, %s, %s)",
                    (migration.version, migration.name, migration.checksum),
                )
                conn.commit()
                applied_names.append(migration.name)
        finally:
            conn.rollback()  # a failed file leaves the transaction aborted; the unlock needs a clean one
            conn.execute("SELECT pg_advisory_unlock(%s)", (MIGRATION_LOCK_KEY,))
            conn.commit()
    return applied_names
