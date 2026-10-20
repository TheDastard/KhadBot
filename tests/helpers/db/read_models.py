"""
tests/helpers/db/read_models.py

Projected read models held in memory, for tests that check the read-model path without Postgres and for the hosted
tests' expected values:

- ``project_reports`` projects every pull of some reports, as the writer would store them.
- ``InMemoryQueries`` answers ``db/queries.py``'s report, report-rows and findings-hashes queries from those
  projections and a findings store, with the same filters and order as the SQL, so ``night.SqlSource`` runs
  unchanged against it.
- ``expected_progression`` is the progression query worked out in Python.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

import pytest

from khadbot.db import queries
from khadbot.db.read_models import ReadModelRows, ReportRow, project, report_row
from khadbot.encounter.evidence import EncounterData
from khadbot.encounter.findings import FindingsStore
from khadbot.encounter.ingest.fetch import RawPageCache


@dataclass
class Projected:
    """Reports and their pulls' rows, as the read-model tables would hold them."""

    reports: dict[str, ReportRow] = field(default_factory=dict)
    pulls: dict[str, list[ReadModelRows]] = field(default_factory=dict)  # report code -> rows per pull

    def all_rows(self) -> list[ReadModelRows]:
        return [rows for per_report in self.pulls.values() for rows in per_report]


def project_reports(data: EncounterData, raw: RawPageCache | None, codes: list[str]) -> Projected:
    """Every stored pull of ``codes``, projected from the records ``data`` serves."""
    out = Projected()
    for code in codes:
        out.reports[code] = report_row(code, raw.report_meta(code) if raw is not None else None)
        out.pulls[code] = [project(p, data.records(p.pull_key)) for p in data.store.pulls(code)]
    return out


class _NoPool:
    """Stands in for a ``ConnectionPool``; ``InMemoryQueries`` ignores the connection."""

    @contextmanager
    def connection(self) -> Iterator[None]:
        yield None


NO_POOL: Any = _NoPool()


class InMemoryQueries:
    """``db.queries`` over ``Projected`` rows and a findings store. ``install`` patches it into the module."""

    def __init__(self, projected: Projected, findings: FindingsStore | None = None) -> None:
        self.projected = projected
        self.findings = findings

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(queries, "report", self.report)
        monkeypatch.setattr(queries, "report_rows", self.report_rows)
        monkeypatch.setattr(queries, "findings_hashes", self.findings_hashes)

    def report(self, conn: object, code: str) -> ReportRow | None:
        return self.projected.reports.get(code)

    def report_rows(self, conn: object, code: str) -> queries.ReportRows:
        rows = self.projected.pulls.get(code, [])
        pulls = sorted((r.pull for r in rows), key=lambda p: (p.start_ms, p.fight_id))  # ORDER BY start_ms, fight_id
        deaths = sorted((d for r in rows for d in r.deaths if d.n == 1), key=lambda d: d.fight_id)
        mechanics = sorted(
            (m for r in rows for m in r.mechanics if m.hits > 0 or m.failures > 0),
            key=lambda m: (m.fight_id, m.mechanic_id),
        )
        return queries.ReportRows(self.report(conn, code), pulls, deaths, mechanics)

    def findings_hashes(self, conn: object, pull_keys: list[str]) -> list[queries.FindingsHashes]:
        if self.findings is None:
            return []
        out = []
        for key in sorted(set(pull_keys)):
            for pf in self.findings.for_pull(key):  # unflagged, newest first
                produced = pf.produced_by
                out.append(queries.FindingsHashes(key, produced.events_hashes.get(key), produced.encfile_hash))
        return out


def expected_progression(projected: Projected, encounter_id: int, difficulty: int | None) -> queries.Progression:
    """What ``queries.progression`` should return over ``projected``."""
    nights = []
    failures: list[queries.MechanicNight] = []
    reports = sorted(
        projected.reports.values(),
        key=lambda r: (r.start_time_ms is None, r.start_time_ms or 0, r.code),  # start NULLS LAST, then code
    )
    for report in reports:
        rows = [
            r
            for r in projected.pulls.get(report.code, [])
            if r.pull.encounter_id == encounter_id and r.pull.difficulty == difficulty
        ]
        if not rows:
            continue
        hps = [0.0 if r.pull.kill else r.pull.boss_hp_pct for r in rows]
        known = [h for h in hps if h is not None]
        nights.append(
            queries.NightProgress(
                report_code=report.code,
                title=report.title,
                start_time_ms=report.start_time_ms,
                pulls=len(rows),
                kills=sum(r.pull.kill for r in rows),
                best_boss_hp_pct=min(known) if known else None,
            )
        )
        per_mechanic: dict[str, list[Any]] = {}
        for r in rows:
            for m in r.mechanics:
                per_mechanic.setdefault(m.mechanic_id, []).append(m)
        night = [
            queries.MechanicNight(
                report_code=report.code,
                mechanic_id=mid,
                name=max((m.name for m in ms if m.name is not None), default=None),
                failures=sum(m.failures for m in ms),
                pulls_failed=sum(1 for m in ms if m.failures > 0),
            )
            for mid, ms in per_mechanic.items()
            if sum(m.failures for m in ms) > 0
        ]
        failures.extend(sorted(night, key=lambda x: (-x.failures, x.mechanic_id)))
    return queries.Progression(encounter_id, difficulty, nights, failures)
