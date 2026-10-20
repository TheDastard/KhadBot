"""
tests/unit/db/test_migrate.py

Migration file discovery and validation. Applying migrations needs Postgres and is tested under
tests/integration/hosted/.
"""

from __future__ import annotations

import pytest

from khadbot.db.migrate import MIGRATIONS_DIR, MigrationError, load_migrations


def _write(directory, name, text="SELECT 1;\n", *, crlf=False):
    data = text.replace("\n", "\r\n") if crlf else text
    (directory / name).write_bytes(data.encode())


def test_packaged_migrations_are_numbered_from_one():
    migrations = load_migrations()
    assert [m.version for m in migrations] == list(range(1, len(migrations) + 1))
    assert migrations[0].name == "0001_findings"
    assert MIGRATIONS_DIR.is_dir()


def test_migrations_load_in_numeric_order(tmp_path):
    _write(tmp_path, "0002_b.sql")
    _write(tmp_path, "0001_a.sql")
    assert [m.name for m in load_migrations(tmp_path)] == ["0001_a", "0002_b"]


def test_empty_directory_has_no_migrations(tmp_path):
    assert load_migrations(tmp_path) == []


@pytest.mark.parametrize("name", ["1_a.sql", "0001-a.sql", "0001_A.sql", "0001.sql", "readme_0001.sql"])
def test_misnamed_file_is_rejected(tmp_path, name):
    _write(tmp_path, name)
    with pytest.raises(MigrationError, match="must be named"):
        load_migrations(tmp_path)


def test_gap_is_rejected(tmp_path):
    _write(tmp_path, "0001_a.sql")
    _write(tmp_path, "0003_c.sql")
    with pytest.raises(MigrationError, match="0003_c"):
        load_migrations(tmp_path)


def test_duplicate_number_is_rejected(tmp_path):
    _write(tmp_path, "0001_a.sql")
    _write(tmp_path, "0001_b.sql")
    with pytest.raises(MigrationError, match="0001_b"):
        load_migrations(tmp_path)


def test_checksum_ignores_line_endings(tmp_path):
    lf, crlf = tmp_path / "lf", tmp_path / "crlf"
    lf.mkdir()
    crlf.mkdir()
    _write(lf, "0001_a.sql", "SELECT 1;\nSELECT 2;\n")
    _write(crlf, "0001_a.sql", "SELECT 1;\nSELECT 2;\n", crlf=True)
    assert load_migrations(lf)[0].checksum == load_migrations(crlf)[0].checksum


def test_checksum_changes_with_content(tmp_path):
    _write(tmp_path, "0001_a.sql", "SELECT 1;\n")
    before = load_migrations(tmp_path)[0].checksum
    _write(tmp_path, "0001_a.sql", "SELECT 2;\n")
    assert load_migrations(tmp_path)[0].checksum != before
