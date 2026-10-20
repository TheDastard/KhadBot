"""
tests/unit/db/test_read_models.py

The read-model projection over every fixture pull, checked field by field against the records it came from; the
row types against the migration's columns; and the writer's control flow against a scripted connection. Writing
to a real database is tested under tests/integration/hosted/.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

import pytest

from khadbot.db.migrate import MIGRATIONS_DIR
from khadbot.db.read_models import (
    READ_MODEL_VERSION,
    CooldownRow,
    DeathRow,
    MechanicRow,
    PullRow,
    ReportRow,
    WindowRow,
    columns,
    project,
    report_row,
    write_pull,
)
from khadbot.encounter.digest import RECORDS_VERSION
from khadbot.encounter.records_store import RecordsStore, records_hash
from khadbot.storage import LocalBlobStore


def all_records(ds) -> list:
    """Every stored pull's records in ``ds``, as the app serves them."""
    return [ds.ctx.data.records(p.pull_key) for code in ds.ctx.data.store.reports() for p in ds.store.pulls(code)]


# ===========================================================================
# Projection
# ===========================================================================


def test_projection_matches_the_records(dataset, tmp_path):
    records_store = RecordsStore(LocalBlobStore(tmp_path))
    checked = 0
    for r in all_records(dataset):
        rows = project(r.pull, r)
        p, d = rows.pull, r.digest
        code, fight = r.pull.report_code, r.pull.fight_id

        assert (p.report_code, p.fight_id, p.pull_key) == (code, fight, r.pull.pull_key)
        assert (p.encounter_id, p.encounter_name, p.difficulty) == (
            r.pull.encounter_id,
            r.pull.encounter_name,
            r.pull.difficulty,
        )
        assert (p.kill, p.start_ms, p.end_ms, p.duration_ms) == (
            r.pull.kill,
            r.pull.start_ms,
            r.pull.end_ms,
            r.pull.duration_ms,
        )
        assert (p.duration_ms, p.boss_pct, p.boss_hp_pct) == (d.duration_ms, d.boss_pct, d.boss_hp_pct)
        assert (p.phase_reached, p.coverage, p.cutoff_ms, p.anomalies) == (
            d.phase_reached,
            d.coverage,
            d.cutoff_ms,
            d.anomalies,
        )
        assert p.has_encounter_file == (r.enc is not None)
        phase_names = {x.id: x.name for x in r.enc.phases} if r.enc else {}
        assert p.phase_name == phase_names.get(d.phase_reached or "")
        assert p.deaths == len(r.deaths)
        assert p.deaths_before_cutoff == sum(not x.after_cutoff for x in r.deaths)
        assert p.events_hash == d.produced_from["events_hash"]
        assert p.encfile_hash == d.produced_from["encfile_hash"]
        assert p.records_hash == records_hash(d.produced_from)
        assert p.records_key == records_store.key(r.pull, d.produced_from)
        assert p.records_version == RECORDS_VERSION
        assert p.read_model_version == READ_MODEL_VERSION

        assert [x.n for x in rows.deaths] == list(range(1, len(r.deaths) + 1))
        for row, death in zip(rows.deaths, r.deaths, strict=True):
            assert (row.report_code, row.fight_id) == (code, fight)
            assert row.ref == death.ref == f"ev:{r.pull.pull_key}:death:{row.n}"
            assert (row.actor_id, row.name, row.t_ms, row.phase_id, row.after_cutoff) == (
                death.actor_id,
                death.name,
                death.t_ms,
                death.phase_id,
                death.after_cutoff,
            )
            assert (row.killing_ability_id, row.killing_ability_name, row.overkill, row.time_to_die_ms) == (
                death.killing_ability_id,
                death.killing_ability_name,
                death.overkill,
                death.time_to_die_ms,
            )

        mech_names = {x.id: x.name for x in r.enc.mechanics} if r.enc else {}
        assert [m.mechanic_id for m in rows.mechanics] == [m.mechanic_id for m in r.mechanics]
        for row, stat in zip(rows.mechanics, r.mechanics, strict=True):
            assert row.name == mech_names.get(stat.mechanic_id)
            assert (row.ref, row.clusters, row.hits, row.failures, row.damage_taken, row.deaths_in_window) == (
                stat.ref,
                stat.clusters,
                stat.hits,
                stat.failures,
                stat.damage_taken,
                stat.deaths_in_window,
            )

        assert [c.ref for c in rows.cooldowns] == [c.ref for c in r.cooldowns]
        assert len({c.ref for c in rows.cooldowns}) == len(rows.cooldowns)  # the table's key
        for row, use in zip(rows.cooldowns, r.cooldowns, strict=True):
            assert (row.spell_id, row.spell_name, row.category, row.caster, row.t_ms, row.phase_id) == (
                use.spell_id,
                use.spell_name,
                use.category,
                use.caster,
                use.t_ms,
                use.phase_id,
            )

        window_names = {x.id: x.name for x in r.enc.damage_windows} if r.enc else {}
        assert len({(w.window_id, w.occurrence) for w in rows.windows}) == len(rows.windows)  # the table's key
        for row, win in zip(rows.windows, r.windows, strict=True):
            assert (row.window_id, row.occurrence, row.ref, row.t_ms, row.covered) == (
                win.window_id,
                win.occurrence,
                win.ref,
                win.t_ms,
                win.covered,
            )
            assert row.name == window_names.get(win.window_id)
            assert row.defensive_cooldowns_active == win.defensive_cooldowns_active
            assert row.defensive_cooldowns_late == len(win.defensive_cooldowns_late)
        checked += 1
    assert checked == sum(len(dataset.store.pulls(c)) for c in dataset.ctx.data.store.reports())
    assert checked > 0


def test_fixture_pulls_have_rows_of_every_kind(dataset):
    """The fixtures exercise every child table, so the equality tests above and in the hosted job mean something."""
    projected = [project(r.pull, r) for r in all_records(dataset)]
    assert any(rows.deaths for rows in projected)
    assert any(m.failures for rows in projected for m in rows.mechanics)
    assert any(rows.cooldowns for rows in projected)
    assert any(rows.windows for rows in projected)


def test_projection_is_pure(synth_dataset):
    r = all_records(synth_dataset)[0]
    before = (r.pull.model_dump(), r.digest.model_dump(), [d.model_dump() for d in r.deaths])
    assert project(r.pull, r) == project(r.pull, r)
    assert (r.pull.model_dump(), r.digest.model_dump(), [d.model_dump() for d in r.deaths]) == before


def test_projection_rejects_another_pulls_records(synth_dataset):
    first, second = all_records(synth_dataset)[:2]
    with pytest.raises(ValueError, match="cannot be projected"):
        project(first.pull, second)


def test_changed_inputs_change_the_records_hash(synth_dataset):
    r = all_records(synth_dataset)[0]
    produced = {**r.digest.produced_from, "records_version": str(RECORDS_VERSION + 1)}
    changed = r.__class__(**{**r.__dict__, "digest": r.digest.model_copy(update={"produced_from": produced})})
    old, new = project(r.pull, r).pull, project(r.pull, changed).pull
    assert new.records_hash != old.records_hash
    assert new.records_key != old.records_key
    assert new.records_version == RECORDS_VERSION + 1


# ===========================================================================
# Report rows
# ===========================================================================


def test_report_row_from_cached_metadata(dataset):
    raw = dataset.ctx.raw
    for code in dataset.ctx.data.store.reports():
        meta = raw.report_meta(code)
        row = report_row(code, meta)
        assert row.code == code
        assert row.title == dataset.ctx.title(code)  # what the API shows
        assert row.start_time_ms == meta["startTime"]
        assert (row.guild_id, row.guild_name, row.visibility, row.fetched_at) == (None, None, None, None)


@pytest.mark.parametrize(
    ("meta", "title", "start", "fetched_with"),
    [
        (None, None, None, None),
        ({}, None, None, None),
        ({"title": "Night", "startTime": 5, "khadbotFetchedWith": "user"}, "Night", 5, "user"),
        ({"title": 7, "startTime": "5", "khadbotFetchedWith": "someone"}, None, None, None),
        ({"startTime": True, "khadbotFetchedWith": "app"}, None, None, "app"),
    ],
)
def test_report_row_keeps_only_well_typed_values(meta, title, start, fetched_with):
    at = datetime(2026, 10, 20, tzinfo=UTC)
    row = report_row("ABC", meta, fetched_at=at)
    assert (row.title, row.start_time_ms, row.fetched_with, row.fetched_at) == (title, start, fetched_with, at)


# ===========================================================================
# Row types and the migration
# ===========================================================================


def _create_table_columns(sql: str) -> dict[str, list[str]]:
    """Column names of each CREATE TABLE in ``sql``, in order (constraint lines skipped)."""
    out = {}
    for name, body in re.findall(r"CREATE TABLE (\w+) \((.*?)\n\);", sql, flags=re.S):
        cols = []
        for line in body.splitlines():
            line = line.split("--")[0].strip()
            word = line.split(" ", 1)[0] if line else ""
            if word and word.upper() not in {"PRIMARY", "FOREIGN", "UNIQUE", "CHECK", "CONSTRAINT"}:
                cols.append(word)
        out[name] = cols
    return out


def test_row_types_match_the_migration():
    tables = _create_table_columns((MIGRATIONS_DIR / "0002_read_models.sql").read_text(encoding="utf-8"))
    expected = {
        "reports": ReportRow,
        "pulls": PullRow,
        "pull_deaths": DeathRow,
        "pull_mechanics": MechanicRow,
        "pull_cooldowns": CooldownRow,
        "pull_windows": WindowRow,
    }
    assert set(tables) == set(expected)
    for table, row_type in expected.items():
        cols = tables[table]
        if table == "pulls":
            assert cols[-1] == "projected_at"  # filled by the database
            cols = cols[:-1]
        assert cols == columns(row_type), table


# ===========================================================================
# Writer (scripted connection; the real database is in the hosted tests)
# ===========================================================================


class _Result:
    def __init__(self, row: tuple[Any, ...] | None) -> None:
        self.row = row

    def fetchone(self) -> tuple[Any, ...] | None:
        return self.row


class ScriptedConnection:
    """Records every statement; answers the writer's two lookups from ``report`` and ``pull``."""

    def __init__(self, report: tuple[Any, ...] | None, pull: tuple[Any, ...] | None) -> None:
        self.report = report
        self.pull = pull
        self.statements: list[tuple[str, Any]] = []

    def execute(self, sql: str, params: Any = None) -> _Result:
        self.statements.append((sql, params))
        if "FROM reports" in sql:
            return _Result(self.report)
        if "FROM pulls" in sql and sql.lstrip().startswith("SELECT"):
            return _Result(self.pull)
        return _Result(None)

    def cursor(self) -> ScriptedConnection:
        return self

    def __enter__(self) -> ScriptedConnection:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def executemany(self, sql: str, params_seq: Any) -> None:
        self.statements.append((sql, list(params_seq)))

    def verbs(self) -> list[str]:
        return [" ".join(sql.split()[:3]) for sql, _ in self.statements]


@pytest.fixture
def projected(synth_dataset):
    r = next(r for r in all_records(synth_dataset) if r.deaths and r.cooldowns)
    meta = synth_dataset.ctx.raw.report_meta(r.pull.report_code)
    return report_row(r.pull.report_code, meta), project(r.pull, r)


def _stored_report(report: ReportRow) -> tuple[Any, ...]:
    return (
        report.title,
        report.start_time_ms,
        report.guild_id,
        report.guild_name,
        report.visibility,
        report.fetched_with,
        report.fetched_at,
    )


def test_writer_replaces_a_new_pull(projected):
    report, rows = projected
    conn = ScriptedConnection(_stored_report(report), None)
    assert write_pull(conn, report, rows) is True  # type: ignore[arg-type]
    verbs = conn.verbs()
    assert verbs[:2] == ["INSERT INTO reports", "SELECT title, start_time_ms,"]
    assert "UPDATE reports SET" not in verbs  # the stored row already matches
    assert verbs.index("DELETE FROM pulls") < verbs.index("INSERT INTO pulls")
    inserted = {sql.split()[2]: params for sql, params in conn.statements if sql.startswith("INSERT INTO pull_")}
    assert len(inserted["pull_deaths"]) == len(rows.deaths)
    assert len(inserted["pull_mechanics"]) == len(rows.mechanics)
    assert len(inserted["pull_cooldowns"]) == len(rows.cooldowns)
    # Placeholders match the values in every insert.
    for sql, params in conn.statements:
        if sql.startswith("INSERT INTO"):
            first = params[0] if isinstance(params, list) else params
            assert sql.count("%s") == len(first)


def test_writer_skips_a_pull_with_the_same_records(projected):
    report, rows = projected
    conn = ScriptedConnection(_stored_report(report), (rows.pull.records_hash, READ_MODEL_VERSION))
    assert write_pull(conn, report, rows) is False  # type: ignore[arg-type]
    assert not any(v.startswith(("DELETE", "INSERT INTO pull")) for v in conn.verbs())


def test_writer_reprojects_an_older_read_model_version(projected):
    report, rows = projected
    conn = ScriptedConnection(_stored_report(report), (rows.pull.records_hash, READ_MODEL_VERSION - 1))
    assert write_pull(conn, report, rows) is True  # type: ignore[arg-type]
    assert "DELETE FROM pulls" in conn.verbs()


def test_writer_updates_a_changed_report_but_not_from_missing_metadata(projected):
    report, rows = projected
    stale = ("Old title", *_stored_report(report)[1:])
    conn = ScriptedConnection(stale, None)
    write_pull(conn, report, rows)  # type: ignore[arg-type]
    assert "UPDATE reports SET" in conn.verbs()

    conn = ScriptedConnection(stale, None)
    write_pull(conn, report, rows, update_report=False)  # type: ignore[arg-type]
    assert "UPDATE reports SET" not in conn.verbs()


def test_writer_fills_fetched_at_without_clearing_it(projected):
    report, rows = projected
    at = datetime(2026, 10, 20, tzinfo=UTC)
    conn = ScriptedConnection(_stored_report(report), None)
    write_pull(conn, report.__class__(**{**report.__dict__, "fetched_at": at}), rows)  # type: ignore[arg-type]
    assert any(sql.startswith("UPDATE reports SET fetched_at") for sql, _ in conn.statements)

    stored = (*_stored_report(report)[:-1], at)
    conn = ScriptedConnection(stored, None)
    write_pull(conn, report, rows)  # type: ignore[arg-type]  # fetched_at None: leave the stored time
    assert not any(sql.startswith("UPDATE reports SET fetched_at") for sql, _ in conn.statements)


def test_writer_rejects_a_pull_of_another_report(projected):
    report, rows = projected
    other = report.__class__(**{**report.__dict__, "code": "OTHER"})
    with pytest.raises(ValueError, match="is not in report"):
        write_pull(ScriptedConnection(None, None), other, rows)  # type: ignore[arg-type]
