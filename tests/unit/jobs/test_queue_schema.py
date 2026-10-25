"""
tests/unit/jobs/test_queue_schema.py

The migration that creates procrastinate's tables carries its schema verbatim. When the installed procrastinate no
longer matches, this fails: add the new release's migration scripts as a new numbered migration, then update the
pinned version here.
"""

from __future__ import annotations

from importlib.metadata import version

from procrastinate.schema import SchemaManager

from khadbot.db.migrate import MIGRATIONS_DIR

PINNED_VERSION = "3.10.0"


def test_the_migration_holds_the_installed_procrastinate_schema():
    assert version("procrastinate") == PINNED_VERSION, (
        "procrastinate changed: put its migration scripts (procrastinate/sql/migrations) in a new numbered "
        "migration, then update PINNED_VERSION"
    )
    text = (MIGRATIONS_DIR / "0005_procrastinate.sql").read_bytes().decode("utf-8").replace("\r\n", "\n")
    assert f"procrastinate {PINNED_VERSION}" in text.splitlines()[1]
    assert text.endswith(SchemaManager.get_schema().replace("\r\n", "\n"))
