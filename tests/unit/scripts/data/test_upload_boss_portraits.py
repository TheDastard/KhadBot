"""
tests/unit/scripts/data/test_upload_boss_portraits.py

The portrait upload on a temporary folder of small generated images and a local blob store: converted PNGs land under
``art/portraits/``, a re-run skips unchanged files, a changed one is rewritten, a missing portrait is reported, and
``--dry-run`` writes nothing. No exported art is used.
"""

from __future__ import annotations

import io

import pytest
from helpers.scripts import load_script
from PIL import Image

from khadbot.api.boss_art import BossArt
from khadbot.storage import LocalBlobStore

upload = load_script("data", "upload_boss_portraits")


def _png(path, colour) -> None:
    Image.new("RGBA", (8, 4), colour).save(path, format="PNG")


def _registry(*portraits: str | None) -> dict[int, BossArt]:
    return {
        i: BossArt(encounter_id=i, name=f"B{i}", portrait=p, verified=False) for i, p in enumerate(portraits, start=1)
    }


@pytest.fixture
def export(tmp_path):
    folder = tmp_path / "export"
    folder.mkdir()
    _png(folder / "UI-EJ-BOSS-Brute.png", (255, 0, 0, 255))
    _png(folder / "UI-EJ-BOSS-Twins.PNG", (0, 255, 0, 128))
    return folder


@pytest.fixture
def blobs(tmp_path):
    return LocalBlobStore(tmp_path / "data")


def test_writes_converted_portraits_then_skips_unchanged(export, blobs):
    registry = _registry("Brute", "Twins", None, "Brute")
    first = upload.upload_portraits(export, registry, blobs)
    assert (first.written, first.skipped, first.missing) == (["Brute", "Twins"], [], [])
    png = blobs.get("art/portraits/Brute.png")
    assert png is not None
    image = Image.open(io.BytesIO(png))
    assert image.format == "PNG" and image.size == (8, 4) and image.getpixel((0, 0)) == (255, 0, 0, 255)

    second = upload.upload_portraits(export, registry, blobs)
    assert (second.written, second.skipped) == ([], ["Brute", "Twins"])

    _png(export / "UI-EJ-BOSS-Brute.png", (0, 0, 255, 255))
    third = upload.upload_portraits(export, registry, blobs)
    assert (third.written, third.skipped) == (["Brute"], ["Twins"])


def test_reports_a_portrait_the_export_lacks(export, blobs):
    report = upload.upload_portraits(export, _registry("Brute", "Ghost"), blobs)
    assert report.written == ["Brute"] and report.missing == ["Ghost"]
    assert blobs.get("art/portraits/Ghost.png") is None


def test_dry_run_writes_nothing(export, blobs):
    report = upload.upload_portraits(export, _registry("Brute"), blobs, dry_run=True)
    assert report.written == ["Brute"] and blobs.list("art/") == []


def test_main_exit_codes(export, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("KHADBOT_DATA_DIR", str(tmp_path / "main_data"))
    monkeypatch.setenv("KHADBOT_STORAGE", "local")
    registry = tmp_path / "reg.yaml"
    registry.write_text("bosses:\n  - {encounter_id: 1, name: A, portrait: Brute, verified: false}\n", encoding="utf-8")
    assert upload.main([str(export), "--registry", str(registry)]) == 0
    assert (tmp_path / "main_data" / "art" / "portraits" / "Brute.png").is_file()
    registry.write_text("bosses:\n  - {encounter_id: 1, name: A, portrait: Nope, verified: false}\n", encoding="utf-8")
    assert upload.main([str(export), "--registry", str(registry)]) == 1
    assert upload.main([str(tmp_path / "absent")]) == 2
    assert "not found 1: Nope" in capsys.readouterr().out
