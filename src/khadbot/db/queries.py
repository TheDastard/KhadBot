"""
db/queries.py

Reads over the read-model tables (``0002_read_models.sql``): the inputs of the API's report detail and night
summary, and a progression query for one encounter and difficulty across nights (one report is one night).

The report and night queries return the rows ``db/read_models.py`` defines, so the same code turns projected rows
and stored rows into the API's inputs (``api/night.py``); aggregation happens there, not in SQL, so both sources
give identical results. Every query here reads one report or one encounter and difficulty, through the indexes in
the migration.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, LiteralString, cast

from psycopg.rows import class_row

from khadbot.db.read_models import DeathRow, MechanicRow, PullRow, ReportRow, columns

if TYPE_CHECKING:
    from psycopg import Connection

__all__ = [
    "FindingsHashes",
    "MechanicNight",
    "NightProgress",
    "Progression",
    "ReportRows",
    "findings_hashes",
    "progression",
    "report",
    "report_rows",
]


def _select(row_type: type, table: str) -> LiteralString:
    """``SELECT <every column of row_type> FROM table``; column names are the row type's field names."""
    return cast(LiteralString, f"SELECT {', '.join(columns(row_type))} FROM {table}")


# ---------------------------------------------------------------------------
# Report detail and night inputs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReportRows:
    """A report's pulls in time order, with the first death and the hit or failed mechanics of each."""

    report: ReportRow | None
    pulls: list[PullRow]
    deaths: list[DeathRow]  # the first death of each pull that has one
    mechanics: list[MechanicRow]  # mechanics with a hit or a failure


def report(conn: Connection[Any], code: str) -> ReportRow | None:
    """The report's row, or None if it was never projected."""
    with conn.cursor(row_factory=class_row(ReportRow)) as cur:
        return cur.execute(_select(ReportRow, "reports") + " WHERE code = %s", (code,)).fetchone()


def report_rows(conn: Connection[Any], code: str) -> ReportRows:
    """Everything the report detail and night summary read for ``code``; ``pulls`` is empty if none is projected."""
    with conn.cursor(row_factory=class_row(PullRow)) as cur:
        pulls = cur.execute(
            _select(PullRow, "pulls") + " WHERE report_code = %s ORDER BY start_ms, fight_id", (code,)
        ).fetchall()
    with conn.cursor(row_factory=class_row(DeathRow)) as cur:
        deaths = cur.execute(
            _select(DeathRow, "pull_deaths") + " WHERE report_code = %s AND n = 1 ORDER BY fight_id", (code,)
        ).fetchall()
    with conn.cursor(row_factory=class_row(MechanicRow)) as cur:
        mechanics = cur.execute(
            _select(MechanicRow, "pull_mechanics")
            + " WHERE report_code = %s AND (hits > 0 OR failures > 0) ORDER BY fight_id, mechanic_id",
            (code,),
        ).fetchall()
    return ReportRows(report(conn, code), pulls, deaths, mechanics)


@dataclass(frozen=True)
class FindingsHashes:
    """What decides whether one stored, unflagged findings set is stale: the hashes it was produced from."""

    pull_key: str
    events_hash: str | None  # ``produced_by.events_hashes[pull_key]``
    encfile_hash: str | None  # ``produced_by.encfile_hash``


def findings_hashes(conn: Connection[Any], pull_keys: Sequence[str]) -> list[FindingsHashes]:
    """One entry per unflagged findings set stored for any of ``pull_keys`` (the ``findings`` table)."""
    if not pull_keys:
        return []
    with conn.cursor(row_factory=class_row(FindingsHashes)) as cur:
        return cur.execute(
            """
            SELECT pull_key,
                   report -> 'produced_by' -> 'events_hashes' ->> pull_key AS events_hash,
                   report -> 'produced_by' ->> 'encfile_hash' AS encfile_hash
            FROM findings
            WHERE pull_key = ANY(%s) AND NOT flagged
            ORDER BY pull_key, created_at DESC
            """,
            (list(pull_keys),),
        ).fetchall()


# ---------------------------------------------------------------------------
# Progression
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class NightProgress:
    """One night's pulls of an encounter and difficulty."""

    report_code: str
    title: str | None
    start_time_ms: int | None
    pulls: int
    kills: int
    best_boss_hp_pct: float | None  # 0 when the night has a kill; None when no pull has boss health


@dataclass(frozen=True)
class MechanicNight:
    """One mechanic's failures over one night's pulls of an encounter and difficulty."""

    report_code: str
    mechanic_id: str
    name: str | None
    failures: int
    pulls_failed: int


@dataclass(frozen=True)
class Progression:
    encounter_id: int
    difficulty: int | None
    nights: list[NightProgress]  # by report start, earliest first
    mechanics: list[MechanicNight]  # failed mechanics, nights in the same order, then failures desc, mechanic ID


def progression(conn: Connection[Any], encounter_id: int, difficulty: int | None) -> Progression:
    """Best boss health and failures per mechanic for each night that pulled the encounter at ``difficulty``."""
    match: LiteralString
    params: tuple[Any, ...]
    if difficulty is None:
        match, params = "p.encounter_id = %s AND p.difficulty IS NULL", (encounter_id,)
    else:
        match, params = "p.encounter_id = %s AND p.difficulty = %s", (encounter_id, difficulty)
    night_order: LiteralString = "r.start_time_ms NULLS LAST, r.code"
    with conn.cursor(row_factory=class_row(NightProgress)) as cur:
        nights = cur.execute(
            "SELECT r.code AS report_code, r.title, r.start_time_ms, count(*)::int AS pulls, "
            "(count(*) FILTER (WHERE p.kill))::int AS kills, "
            "min(CASE WHEN p.kill THEN 0 ELSE p.boss_hp_pct END) AS best_boss_hp_pct "
            "FROM pulls p JOIN reports r ON r.code = p.report_code "
            "WHERE " + match + " GROUP BY r.code, r.title, r.start_time_ms ORDER BY " + night_order,
            params,
        ).fetchall()
    with conn.cursor(row_factory=class_row(MechanicNight)) as cur:
        mechanics = cur.execute(
            "SELECT p.report_code, m.mechanic_id, max(m.name) AS name, sum(m.failures)::int AS failures, "
            "(count(*) FILTER (WHERE m.failures > 0))::int AS pulls_failed "
            "FROM pulls p JOIN reports r ON r.code = p.report_code "
            "JOIN pull_mechanics m ON m.report_code = p.report_code AND m.fight_id = p.fight_id "
            "WHERE " + match + " GROUP BY p.report_code, r.start_time_ms, r.code, m.mechanic_id "
            "HAVING sum(m.failures) > 0 "
            "ORDER BY " + night_order + ", sum(m.failures) DESC, m.mechanic_id",
            params,
        ).fetchall()
    return Progression(encounter_id, difficulty, nights, mechanics)
