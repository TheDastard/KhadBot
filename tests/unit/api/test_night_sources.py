"""
tests/unit/api/test_night_sources.py

The report detail and night summary from the read-model path equal the record-backed ones, without Postgres:
every fixture report is projected in memory, and ``SqlSource`` runs against ``InMemoryQueries``, which answers the
queries from those rows with the SQL's filters and order. What the SQL itself returns is checked by the hosted
tests (tests/integration/hosted/test_read_models_pg.py).
"""

from __future__ import annotations

import dataclasses
from threading import RLock

import pytest
from helpers.db.read_models import NO_POOL, InMemoryQueries, project_reports
from helpers.encounter.findings import make

from khadbot.api import create_app
from khadbot.api.night import (
    RecordsSource,
    SqlSource,
    build_night,
    build_report_detail,
    facts_from_records,
    facts_from_rows,
    read_source,
)
from khadbot.api.views import ApiError
from khadbot.db.read_models import project
from khadbot.encounter.findings import SqliteFindingsStore
from khadbot.encounter.models import ProducedBy


def codes(ds) -> list[str]:
    return ds.ctx.data.store.reports()


@pytest.fixture
def sql_source(dataset, monkeypatch) -> SqlSource:
    InMemoryQueries(project_reports(dataset.ctx.data, dataset.ctx.raw, codes(dataset))).install(monkeypatch)
    return SqlSource(NO_POOL)


def test_facts_from_projected_rows_equal_facts_from_records(dataset):
    for code in codes(dataset):
        for p in dataset.store.pulls(code):
            r = dataset.ctx.data.records(p.pull_key)
            rows = project(p, r)
            assert facts_from_rows(rows.pull, rows.deaths, rows.mechanics) == facts_from_records(r)


def test_night_and_report_detail_match_the_records(dataset, sql_source):
    records = RecordsSource(dataset.ctx)
    for code in codes(dataset):
        assert build_night(sql_source, code) == build_night(records, code)
        assert build_report_detail(sql_source, code) == build_report_detail(records, code)


def test_record_source_matches_the_api(dataset):
    """The routes go through the same builders: what the client gets is the record-backed summary."""
    code = dataset.code
    night = build_night(RecordsSource(dataset.ctx), code)
    assert dataset.client.get(f"/v1/reports/{code}/night").json() == night.model_dump(mode="json")
    detail = build_report_detail(RecordsSource(dataset.ctx), code)
    assert dataset.client.get(f"/v1/reports/{code}").json() == detail.model_dump(mode="json")


def test_sql_source_unknown_report(sql_source):
    with pytest.raises(ApiError) as exc:
        sql_source.pulls("NOPE")
    assert (exc.value.status, exc.value.code) == (404, "report_not_found")
    assert sql_source.title("NOPE") is None


def test_app_serves_from_the_given_source(synth_dataset, monkeypatch):
    InMemoryQueries(project_reports(synth_dataset.ctx.data, synth_dataset.ctx.raw, ["SYNTH01"])).install(monkeypatch)
    source = SqlSource(NO_POOL)
    app = create_app(
        synth_dataset.ctx.data, synth_dataset.ctx.findings, data_dir=synth_dataset.data_dir, read_source=source
    )
    assert read_source(app.state.ctx) is source
    assert isinstance(read_source(synth_dataset.ctx), RecordsSource)  # local mode's default
    from fastapi.testclient import TestClient

    client = TestClient(app)
    assert (
        client.get("/v1/reports/SYNTH01/night").json() == synth_dataset.client.get("/v1/reports/SYNTH01/night").json()
    )
    assert client.get("/v1/reports/SYNTH01").json() == synth_dataset.client.get("/v1/reports/SYNTH01").json()


# ===========================================================================
# Findings
# ===========================================================================


def test_findings_counts_match_the_records(synth_dataset, monkeypatch, tmp_path):
    """Fresh, stale (events or encounter file changed) and flagged findings count the same on both paths."""
    ds = synth_dataset
    store = SqliteFindingsStore(tmp_path / "khadbot.sqlite")
    ctx = dataclasses.replace(ds.ctx, findings=store, lock=RLock())
    pulls = ds.store.pulls(ds.code)
    assert len(pulls) >= 2
    first, second = (facts_from_records(ds.ctx.data.records(p.pull_key)) for p in pulls[:2])

    def put(pull, key, events_hash, encfile_hash, *, flag=False, minutes=0):
        pf = make(f"{key}0000-1", pull=pull.pull_key, key=key, minutes=minutes)
        produced = ProducedBy(
            model_id="m",
            prompt_version="p",
            graph_version="1",
            encfile_hash=encfile_hash,
            events_hashes={pull.pull_key: events_hash} if events_hash is not None else {},
        )
        store.put(pf.model_copy(update={"produced_by": produced}))
        if flag:
            store.flag(f"{key}0000-1")

    put(first, "a", first.events_hash, first.encfile_hash)  # fresh
    put(first, "b", first.events_hash, None, minutes=1)  # no encounter file recorded: still fresh
    put(second, "c", "old-events", second.encfile_hash)  # stale: events changed
    put(second, "d", second.events_hash, "old-file", flag=True)  # flagged: left out

    InMemoryQueries(project_reports(ds.ctx.data, ds.ctx.raw, [ds.code]), store).install(monkeypatch)
    records_night = build_night(RecordsSource(ctx), ds.code)
    sql_night = build_night(SqlSource(NO_POOL), ds.code)
    assert sql_night == records_night
    section = next(s for s in records_night.sections if any(t.fight == first.fight_id for t in s.trend))
    assert (section.findings_available, section.findings_stale) == (2, 1)

    # An encounter file changed since the review is stale on both paths too.
    put(first, "e", first.events_hash, "old-file", minutes=2)
    assert build_night(SqlSource(NO_POOL), ds.code) == build_night(RecordsSource(ctx), ds.code)
    section = next(s for s in build_night(RecordsSource(ctx), ds.code).sections)
    assert section.findings_stale == 2
