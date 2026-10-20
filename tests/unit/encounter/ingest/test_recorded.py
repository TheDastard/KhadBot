"""
tests/unit/encounter/ingest/test_recorded.py

Offline ingest of recorded raw pages (ingest/recorded.py): which reports count as recorded (Git LFS pointers do not),
which fights have pages, and that the ingest is offline over those fights.
"""

from __future__ import annotations

from khadbot.encounter.ingest import RECORDED_BUFF_IDS, ingest_recorded, recorded_fights, recorded_reports
from khadbot.encounter.ingest.recorded import is_recorded
from khadbot.encounter.store import ParquetEventStore

POINTER = b"version https://git-lfs.github.com/spec/v1\noid sha256:abc\nsize 12\n"


def _report(root, code, body: bytes, fights=()):
    (root / code).mkdir(parents=True)
    (root / code / "report.json").write_bytes(body)
    for f in fights:
        (root / code / str(f)).mkdir()


def test_lfs_pointers_and_missing_metadata_are_not_recorded(tmp_path):
    _report(tmp_path, "REAL", b'{"title": "x"}', fights=[3, 12])
    _report(tmp_path, "POINTER", POINTER)
    (tmp_path / "NOMETA").mkdir()
    (tmp_path / "notes.txt").write_text("not a report")
    assert is_recorded(tmp_path, "REAL")
    assert not is_recorded(tmp_path, "POINTER")
    assert not is_recorded(tmp_path, "NOMETA")
    assert recorded_reports(tmp_path) == ["REAL"]
    assert recorded_fights(tmp_path, "REAL") == [3, 12]


def test_synthetic_reports_are_excluded_by_default(tmp_path):
    _report(tmp_path, "SYNTH01", b"{}")
    _report(tmp_path, "REAL", b"{}")
    assert recorded_reports(tmp_path) == ["REAL"]
    assert recorded_reports(tmp_path, exclude=()) == ["REAL", "SYNTH01"]
    assert recorded_reports(tmp_path / "missing") == []


async def test_ingest_recorded_is_offline_over_recorded_fights(tmp_path, monkeypatch):
    _report(tmp_path, "REAL", b"{}", fights=[12, 3])
    calls = []

    async def fake_ingest(code, fight_ids, **kwargs):
        calls.append((code, fight_ids, kwargs))
        return "result"

    monkeypatch.setattr("khadbot.encounter.ingest.recorded.ingest_report", fake_ingest)
    store = ParquetEventStore(tmp_path / "events")
    assert await ingest_recorded("REAL", tmp_path, store) == "result"
    code, fights, kwargs = calls[0]
    assert (code, fights) == ("REAL", [3, 12])
    assert kwargs["offline"] is True
    assert kwargs["store"] is store
    assert kwargs["cache"].blobs.root == tmp_path
    assert kwargs["defensive_spell_ids"] == RECORDED_BUFF_IDS
