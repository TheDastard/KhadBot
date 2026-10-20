"""
tests/integration/hosted/test_read_models_pg.py

The read-model tables against a real database: every fixture report projected through ``project_pull``, then the
SQL-backed night summary and report detail compared with the record-backed ones, exactly. Also: re-projection
writes nothing, changed records replace a pull's rows, concurrent projections of one pull write once, findings in
Postgres count as they do in SQLite, and the progression query matches the same values worked out in Python.

``dataset`` runs over the synthetic report always and over the recorded reports when their Git LFS pages are
pulled (the CI hosted job checks out without LFS, so it covers the synthetic report only).
"""

from __future__ import annotations

import dataclasses
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import RLock

import pytest
from helpers.db.read_models import expected_progression, project_reports
from helpers.encounter.findings import make

from khadbot.api.night import RecordsSource, SqlSource, build_night, build_report_detail, facts_from_records
from khadbot.api.views import ApiError
from khadbot.db import PostgresFindingsStore, migrate, queries
from khadbot.db.read_models import project_pull
from khadbot.encounter.findings import SqliteFindingsStore
from khadbot.encounter.models import ProducedBy

TABLES = ("reports", "pulls", "pull_deaths", "pull_mechanics", "pull_cooldowns", "pull_windows")


@pytest.fixture
def db(pool):
    migrate(pool)
    return pool


def codes(ds) -> list[str]:
    return ds.ctx.data.store.reports()


def project_all(pool, ds) -> list[bool]:
    """Project every stored pull of ``ds``; what ``project_pull`` returned for each."""
    return [
        project_pull(pool, ds.ctx.data.records(p.pull_key), ds.ctx.raw.report_meta(code))
        for code in codes(ds)
        for p in ds.store.pulls(code)
    ]


def snapshot(pool) -> dict[str, list[tuple]]:
    """Every row of every read-model table, in a fixed order."""
    with pool.connection() as conn:
        return {t: sorted(conn.execute(f"SELECT * FROM {t}").fetchall(), key=repr) for t in TABLES}


def counts(pool, code: str, fight: int) -> dict[str, int]:
    with pool.connection() as conn:
        return {
            t: conn.execute(
                f"SELECT count(*) FROM {t} WHERE report_code = %s AND fight_id = %s", (code, fight)
            ).fetchone()[0]
            for t in TABLES[1:]
        }


# ===========================================================================
# Night summary and report detail
# ===========================================================================


def test_sql_night_and_report_detail_equal_the_records(db, dataset):
    assert all(project_all(db, dataset))
    sql, records = SqlSource(db), RecordsSource(dataset.ctx)
    for code in codes(dataset):
        assert build_night(sql, code) == build_night(records, code)
        assert build_report_detail(sql, code) == build_report_detail(records, code)
        assert sql.title(code) == dataset.ctx.title(code)


def test_unknown_report_is_not_found(db, synth_dataset):
    project_all(db, synth_dataset)
    with pytest.raises(ApiError) as exc:
        SqlSource(db).pulls("NOPE")
    assert exc.value.code == "report_not_found"


def test_rows_hold_every_pull(db, dataset):
    project_all(db, dataset)
    for code in codes(dataset):
        for p in dataset.store.pulls(code):
            r = dataset.ctx.data.records(p.pull_key)
            assert counts(db, code, p.fight_id) == {
                "pulls": 1,
                "pull_deaths": len(r.deaths),
                "pull_mechanics": len(r.mechanics),
                "pull_cooldowns": len(r.cooldowns),
                "pull_windows": len(r.windows),
            }


# ===========================================================================
# Idempotency and replacement
# ===========================================================================


def test_reprojection_changes_nothing(db, dataset):
    project_all(db, dataset)
    before = snapshot(db)
    assert not any(project_all(db, dataset))
    assert snapshot(db) == before


def test_changed_records_replace_the_pulls_rows(db, synth_dataset):
    ds = synth_dataset
    project_all(db, ds)
    r = next(r for r in (ds.ctx.data.records(p.pull_key) for p in ds.store.pulls(ds.code)) if r.deaths)
    code, fight = r.pull.report_code, r.pull.fight_id
    before = counts(db, code, fight)

    produced = {**r.digest.produced_from, "records_version": "999"}
    changed = dataclasses.replace(
        r, deaths=r.deaths[:1], digest=r.digest.model_copy(update={"produced_from": produced})
    )
    assert project_pull(db, changed, ds.ctx.raw.report_meta(code)) is True
    assert counts(db, code, fight) == {**before, "pull_deaths": 1}
    with db.connection() as conn:
        (version,) = conn.execute(
            "SELECT records_version FROM pulls WHERE report_code = %s AND fight_id = %s", (code, fight)
        ).fetchone()
    assert version == 999

    assert project_pull(db, r, ds.ctx.raw.report_meta(code)) is True  # and back
    assert counts(db, code, fight) == before


def test_concurrent_projections_of_one_pull_write_once(db, synth_dataset):
    ds = synth_dataset
    r = ds.ctx.data.records(ds.store.pulls(ds.code)[0].pull_key)
    meta = ds.ctx.raw.report_meta(ds.code)
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(lambda _: project_pull(db, r, meta), range(4)))
    assert sorted(results) == [False, False, False, True]
    assert counts(db, r.pull.report_code, r.pull.fight_id)["pulls"] == 1


def test_report_row_updates_and_keeps_fetched_at(db, synth_dataset):
    ds = synth_dataset
    r = ds.ctx.data.records(ds.store.pulls(ds.code)[0].pull_key)
    meta = ds.ctx.raw.report_meta(ds.code)
    at = datetime(2026, 10, 20, 12, tzinfo=UTC)
    project_pull(db, r, {**meta, "title": "Old", "khadbotFetchedWith": "user"}, fetched_at=at)
    project_pull(db, r, meta)  # metadata refreshed, no fetch time given
    project_pull(db, r, None)  # no metadata: the stored row stays
    with db.connection() as conn:
        row = queries.report(conn, ds.code)
    assert row is not None
    assert (row.title, row.fetched_with, row.fetched_at) == (meta["title"], meta.get("khadbotFetchedWith"), at)
    assert row.start_time_ms == meta["startTime"]


# ===========================================================================
# Findings
# ===========================================================================


def test_findings_in_postgres_count_as_in_sqlite(db, synth_dataset, tmp_path):
    ds = synth_dataset
    project_all(db, ds)
    sqlite = SqliteFindingsStore(tmp_path / "khadbot.sqlite")
    pg = PostgresFindingsStore(db)
    first, second = (facts_from_records(ds.ctx.data.records(p.pull_key)) for p in ds.store.pulls(ds.code)[:2])

    def put(f, key, events_hash, encfile_hash, *, flag=False, minutes=0):
        produced = ProducedBy(
            model_id="m",
            prompt_version="p",
            graph_version="1",
            encfile_hash=encfile_hash,
            events_hashes={f.pull_key: events_hash},
        )
        pf = make(f"{key}0000-1", pull=f.pull_key, key=key, minutes=minutes).model_copy(
            update={"produced_by": produced}
        )
        for store in (sqlite, pg):
            store.put(pf)
            if flag:
                store.flag(f"{key}0000-1")

    put(first, "a", first.events_hash, first.encfile_hash)
    put(first, "b", first.events_hash, "old-file", minutes=1)  # stale: encounter file changed
    put(second, "c", "old-events", None)  # stale: events changed
    put(second, "d", second.events_hash, second.encfile_hash, flag=True)  # flagged: left out

    ctx = dataclasses.replace(ds.ctx, findings=sqlite, lock=RLock())
    night = build_night(SqlSource(db), ds.code)
    assert night == build_night(RecordsSource(ctx), ds.code)
    assert sum(s.findings_available for s in night.sections) == 2
    assert sum(s.findings_stale for s in night.sections) == 2


# ===========================================================================
# Progression
# ===========================================================================


def test_progression_matches_the_rows(db, dataset):
    project_all(db, dataset)
    projected = project_reports(dataset.ctx.data, dataset.ctx.raw, codes(dataset))
    groups = {(r.pull.encounter_id, r.pull.difficulty) for r in projected.all_rows()}
    assert groups
    for encounter_id, difficulty in sorted(groups, key=repr):
        with db.connection() as conn:
            got = queries.progression(conn, encounter_id, difficulty)
        assert got == expected_progression(projected, encounter_id, difficulty)
        assert got.nights
    with db.connection() as conn:
        assert queries.progression(conn, -1, None) == queries.Progression(-1, None, [], [])


def test_query_plans(db, dataset, capsys):
    """Every read runs; ``pytest -s -k query_plans`` prints its plan on the seeded database."""
    project_all(db, dataset)
    code = codes(dataset)[0]
    pull = dataset.store.pulls(code)[0]
    with db.connection() as conn:
        conn.execute("ANALYZE")
        statements = [
            ("pulls of a report", "SELECT * FROM pulls WHERE report_code = %s ORDER BY start_ms, fight_id", (code,)),
            ("first deaths", "SELECT * FROM pull_deaths WHERE report_code = %s AND n = 1 ORDER BY fight_id", (code,)),
            (
                "active mechanics",
                "SELECT * FROM pull_mechanics WHERE report_code = %s AND (hits > 0 OR failures > 0) "
                "ORDER BY fight_id, mechanic_id",
                (code,),
            ),
            (
                "progression",
                "SELECT p.report_code, min(p.boss_hp_pct) FROM pulls p JOIN reports r ON r.code = p.report_code "
                "WHERE p.encounter_id = %s AND p.difficulty = %s GROUP BY p.report_code",
                (pull.encounter_id, pull.difficulty),
            ),
        ]
        for name, sql, params in statements:
            plan = "\n".join(row[0] for row in conn.execute("EXPLAIN " + sql, params).fetchall())
            with capsys.disabled():
                print(f"\n-- {name}\n{plan}")
            assert plan
