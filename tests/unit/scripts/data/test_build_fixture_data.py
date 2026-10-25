"""
tests/unit/scripts/data/test_build_fixture_data.py

scripts/data/build_fixture_data.py: it builds a local data directory from every recorded fixture report offline (no
network, no Postgres or bucket even when the environment names them), digests each pull so the annotation cache and
the pull records are filled, a second run changes nothing, and ``create_app`` serves the result in local mode. Needs
the fixtures' Git LFS pages; without them the build test skips.
"""

from __future__ import annotations

import asyncio
import hashlib
import socket
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from helpers.encounter.real_fixtures import REAL_PULLS, REAL_REPORTS, WCL_FIXTURES
from helpers.scripts import load_script

from khadbot.api import create_app

script = load_script("data", "build_fixture_data")

needs_pages = pytest.mark.skipif(not REAL_REPORTS, reason="recorded fixtures need `git lfs pull`")


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Fail any connection off this machine (loopback stays open: asyncio's socketpair uses it on Windows)."""
    real_connect = socket.socket.connect

    def guarded(self, address):
        host = address[0] if isinstance(address, tuple) else address
        if host not in {"127.0.0.1", "::1", "localhost"}:
            raise AssertionError(f"build tried to connect to {address!r}")
        return real_connect(self, address)

    monkeypatch.setattr(socket.socket, "connect", guarded)


def manifest(root: Path) -> dict[str, str]:
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in root.rglob("*")
        if p.is_file()
    }


@pytest.fixture(scope="module")
def built(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("fixture_data")
    assert asyncio.run(script.build(WCL_FIXTURES, out)) == []
    return out


@needs_pages
class TestBuild:
    def test_every_recorded_report_is_ingested_and_digested(self, built):
        from khadbot.encounter.store import ParquetEventStore
        from khadbot.storage import LocalBlobStore

        store = ParquetEventStore(LocalBlobStore(built))
        assert store.reports() == sorted(REAL_REPORTS)
        assert sorted(f"{p.report_code}:{p.fight_id}" for r in store.reports() for p in store.pulls(r)) == sorted(
            REAL_PULLS
        )
        assert list((built / "annotated").rglob("*")), "digesting fills the annotation cache"
        assert list((built / "records").rglob("*")), "digesting stores the pull records"
        for code in REAL_REPORTS:
            assert (built / "raw" / code / "report.json").is_file()
        assert not list((built / "raw").rglob("page_*.json")), "event pages are read from the fixtures, not copied"

    def test_building_again_changes_nothing(self, built, tmp_path):
        before = manifest(built)
        assert asyncio.run(script.build(WCL_FIXTURES, built)) == []
        assert manifest(built) == before

    def test_the_environment_cannot_redirect_it_to_hosted_stores(self, tmp_path, monkeypatch):
        monkeypatch.setenv("KHADBOT_MODE", "hosted")
        monkeypatch.setenv("KHADBOT_STORAGE", "s3")
        monkeypatch.setenv("DATABASE_URL", "postgresql://nobody@127.0.0.1:1/none")
        monkeypatch.setenv("KHADBOT_S3_BUCKET", "not-a-bucket")
        assert asyncio.run(script.build(WCL_FIXTURES, tmp_path)) == []
        assert (tmp_path / "events").is_dir()

    def test_the_result_is_served_in_local_mode(self, built):
        client = TestClient(create_app(mode="local", data_dir=built))
        health = client.get("/v1/health").json()
        assert health["mode"] == "local" and not any(health["features"].values())
        assert [r["code"] for r in client.get("/v1/reports").json()["reports"]] == sorted(REAL_REPORTS)
        code = REAL_REPORTS[0]
        assert client.get(f"/v1/reports/{code}/night").status_code == 200


@needs_pages
class TestOnly:
    def test_only_builds_the_named_report(self, tmp_path):
        from khadbot.encounter.store import ParquetEventStore
        from khadbot.storage import LocalBlobStore

        code = min(REAL_REPORTS, key=lambda c: sum(p.stat().st_size for p in (WCL_FIXTURES / c).rglob("*")))
        assert asyncio.run(script.build(WCL_FIXTURES, tmp_path, [code, code])) == []
        store = ParquetEventStore(LocalBlobStore(tmp_path))
        assert store.reports() == [code]
        assert sorted(f"{code}:{p.fight_id}" for p in store.pulls(code)) == sorted(
            k for k in REAL_PULLS if k.startswith(f"{code}:")
        )
        assert [p.name for p in (tmp_path / "raw").iterdir()] == [code]
        assert list((tmp_path / "records").rglob("*")), "the named report is digested"

    def test_a_code_without_pages_fails_and_builds_nothing(self, tmp_path):
        failures = asyncio.run(script.build(WCL_FIXTURES, tmp_path, [REAL_REPORTS[0], "SYNTH01", "NOPE"]))
        assert len(failures) == 2
        assert "SYNTH01" in failures[0] and "NOPE" in failures[1]
        assert all("git lfs pull --include" in f for f in failures)
        assert not list(tmp_path.iterdir())


def test_missing_recordings_are_reported_not_raised(tmp_path):
    failures = asyncio.run(script.build(tmp_path / "no-fixtures", tmp_path / "out"))
    assert len(failures) == 1 and "git lfs pull" in failures[0]


def test_only_names_a_missing_report_with_its_lfs_include(tmp_path):
    failures = asyncio.run(script.build(tmp_path / "no-fixtures", tmp_path / "out", ["ABC123"]))
    assert failures == [
        f"No recorded pages for ABC123 under {tmp_path / 'no-fixtures'}. Run "
        '`git lfs pull --include "tests/fixtures/encounter/wcl/ABC123/**"` first; SYNTH01 is never built here.'
    ]
