"""
tests/integration/hosted/test_migrate_pg.py

The migration runner against a real database: builds the schema, is idempotent, rolls a failed file back,
detects an edited file, and is safe to run from several threads at once.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest

from khadbot.db import MigrationError, migrate
from khadbot.db.migrate import load_migrations


def _tables(pool) -> set[str]:
    with pool.connection() as conn:
        rows = conn.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = current_schema()")
        return {r[0] for r in rows.fetchall()}


def _recorded(pool) -> list[str]:
    with pool.connection() as conn:
        return [r[0] for r in conn.execute("SELECT name FROM schema_migrations ORDER BY version").fetchall()]


def test_builds_the_schema_on_an_empty_database(pool):
    applied = migrate(pool)
    assert applied == [m.name for m in load_migrations()]
    assert {"schema_migrations", "findings"} <= _tables(pool)
    assert _recorded(pool) == applied


def test_second_run_applies_nothing(pool):
    migrate(pool)
    assert migrate(pool) == []


def test_failed_file_leaves_no_trace_and_stops_the_run(pool, tmp_path):
    (tmp_path / "0001_ok.sql").write_text("CREATE TABLE ok_table (id int);\n")
    (tmp_path / "0002_bad.sql").write_text("CREATE TABLE half (id int);\nSELECT * FROM missing_table;\n")
    (tmp_path / "0003_never.sql").write_text("CREATE TABLE never (id int);\n")
    with pytest.raises(Exception, match="missing_table"):
        migrate(pool, tmp_path)
    assert _recorded(pool) == ["0001_ok"]
    assert "ok_table" in _tables(pool)
    assert not {"half", "never"} & _tables(pool)

    # The lock was released, and fixing the file lets the run continue from where it stopped.
    (tmp_path / "0002_bad.sql").write_text("CREATE TABLE half (id int);\n")
    assert migrate(pool, tmp_path) == ["0002_bad", "0003_never"]


def test_edited_applied_file_is_an_error(pool, tmp_path):
    path = tmp_path / "0001_a.sql"
    path.write_text("CREATE TABLE a (id int);\n")
    migrate(pool, tmp_path)
    path.write_text("CREATE TABLE a (id int, extra int);\n")
    with pytest.raises(MigrationError, match="different content"):
        migrate(pool, tmp_path)


def test_concurrent_runners_apply_each_file_once(pool):
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(lambda _: migrate(pool), range(4)))
    expected = [m.name for m in load_migrations()]
    assert sorted(results, key=len) == [[], [], [], expected]  # exactly one runner applied them
    assert _recorded(pool) == expected
