"""
tests/unit/api/test_views.py

Cached findings through the API: returned as stored, marked stale once the pull's events or its encounter file
change, and counted in the night summary. Each test builds its own synthetic data directory, because it edits it.
"""

from __future__ import annotations

import pytest
from helpers.api.datasets import build_synth
from helpers.encounter.findings import make
from helpers.encounter.synth import SYNTH_ENCOUNTER_FILE, install_synth_encounter

from khadbot.encounter.config_files import encfile_hash, load_encounter_view
from khadbot.encounter.models import ProducedBy


@pytest.fixture
def ds(tmp_path):
    return build_synth(tmp_path)


def _store_current(ds, fight: int = 1, key: str = "k1", minutes: int = 0):
    """A cached finding for the pull whose hashes match the current data."""
    pull = next(p for p in ds.store.pulls(ds.code) if p.fight_id == fight)
    pf = make(pull=pull.pull_key, key=key, minutes=minutes)
    produced = ProducedBy(
        model_id="model-a",
        prompt_version="p" * 12,
        graph_version="1",
        encfile_hash=encfile_hash(load_encounter_view(pull.encounter_id, pull.difficulty, ds.enc_root)),
        events_hashes={pull.pull_key: ds.store.events_hash(pull.pull_key) or ""},
    )
    pf = pf.model_copy(update={"produced_by": produced})
    ds.ctx.findings.put(pf)
    return pf


def findings(ds, fight: int = 1) -> dict:
    response = ds.client.get(f"/v1/reports/{ds.code}/pulls/{fight}/findings")
    assert response.status_code == 200, response.text
    return response.json()


def test_no_findings_cached(ds):
    assert findings(ds) == {"findings": [], "truncated": False, "total": 0}
    assert not ds.ctx.findings.db_path.exists()


def test_current_findings_are_not_stale(ds):
    pf = _store_current(ds)
    body = findings(ds)
    assert body["total"] == 1
    (got,) = body["findings"]
    assert got["stale"] is False
    assert {k: v for k, v in got.items() if k != "stale"} == pf.model_dump(mode="json")
    assert findings(ds, 2)["findings"] == []


def test_newest_first(ds):
    _store_current(ds, key="old", minutes=0)
    _store_current(ds, key="new", minutes=5)
    assert [f["cache_key"] for f in findings(ds)["findings"]] == ["new", "old"]


def test_encounter_file_change_makes_findings_stale(ds):
    _store_current(ds)
    text = SYNTH_ENCOUNTER_FILE.read_text(encoding="utf-8")
    edited = text.replace("file_version: 1", "file_version: 2")
    assert edited != text
    install_synth_encounter(ds.enc_root, edited)
    assert findings(ds)["findings"][0]["stale"] is True


def test_events_change_makes_findings_stale(ds):
    pf = _store_current(ds)
    hashes = {pf.pull_key: "different"}
    ds.ctx.findings.put(
        pf.model_copy(update={"produced_by": pf.produced_by.model_copy(update={"events_hashes": hashes})})
    )
    assert findings(ds)["findings"][0]["stale"] is True


def test_flagged_findings_are_left_out(ds):
    pf = _store_current(ds)
    ds.ctx.findings.flag(pf.findings[0].id)
    assert findings(ds)["findings"] == []


def test_night_counts_pulls_with_findings(ds):
    _store_current(ds, fight=1)
    stale = _store_current(ds, fight=2, key="k2")
    produced = stale.produced_by.model_copy(update={"events_hashes": {stale.pull_key: "different"}})
    ds.ctx.findings.put(stale.model_copy(update={"produced_by": produced}))
    (section,) = ds.client.get(f"/v1/reports/{ds.code}/night").json()["sections"]
    assert section["findings_available"] == 2
    assert section["findings_stale"] == 1
