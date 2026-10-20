"""
db/read_models.py

Read models: the per-pull rows behind the night, report and progression views (``0002_read_models.sql``).

``project`` turns one pull's ``PullRecords`` into rows and is pure; ``report_row`` does the same for a report's
cached metadata. ``project_pull`` is the writer: it replaces one pull's rows in a single transaction, keyed by the
pull and its records hash, so projecting the same records twice writes nothing the second time. The rows hold what
the views filter, sort or aggregate on; the full records stay in the records blob (``records_key``).

Each row type's field names are its table's column names, so the writer and ``db/queries.py`` build their column
lists from the dataclass.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, LiteralString, cast

from khadbot.encounter.digest import PullRecords
from khadbot.encounter.ingest.fetch import FETCHED_WITH_KEY
from khadbot.encounter.models import Pull
from khadbot.encounter.records_store import records_hash
from khadbot.storage import join_key

if TYPE_CHECKING:
    from psycopg import Connection
    from psycopg_pool import ConnectionPool

__all__ = [
    "READ_MODEL_VERSION",
    "CooldownRow",
    "DeathRow",
    "MechanicRow",
    "PullRow",
    "ReadModelRows",
    "ReportRow",
    "WindowRow",
    "columns",
    "project",
    "project_pull",
    "report_row",
    "write_pull",
]

# Bump when ``project`` changes what it writes for the same records (a new column, a changed value). Stored on each
# pull row; a row with an older version is re-projected even though its records hash is unchanged.
READ_MODEL_VERSION = 1

# The records store's default prefix: ``records_key`` names the blob a pull's rows came from.
RECORDS_PREFIX = "records"


# ---------------------------------------------------------------------------
# Rows
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReportRow:
    """``reports``: one per ingested report."""

    code: str
    title: str | None
    start_time_ms: int | None
    guild_id: int | None
    guild_name: str | None
    visibility: str | None
    fetched_with: str | None  # "user" or "app", from ``khadbotFetchedWith``
    fetched_at: datetime | None


@dataclass(frozen=True)
class PullRow:
    """``pulls``: one per pull, with the fields the report detail and night summary show."""

    report_code: str
    fight_id: int
    pull_key: str
    encounter_id: int
    encounter_name: str
    difficulty: int | None
    kill: bool
    start_ms: int
    end_ms: int
    duration_ms: int
    boss_pct: float | None
    boss_hp_pct: float | None
    phase_reached: str | None
    phase_name: str | None  # the encounter file's name for ``phase_reached``
    has_encounter_file: bool
    coverage: str
    cutoff_ms: int | None
    deaths: int
    deaths_before_cutoff: int
    anomalies: list[str]
    events_hash: str
    encfile_hash: str
    records_hash: str
    records_key: str
    records_version: int
    read_model_version: int


@dataclass(frozen=True)
class DeathRow:
    """``pull_deaths``: every player death, ``n`` in time order (the ``death:<n>`` ref)."""

    report_code: str
    fight_id: int
    n: int
    ref: str
    actor_id: int
    name: str
    t_ms: int
    phase_id: str | None
    killing_ability_id: int | None
    killing_ability_name: str | None
    overkill: int | None
    time_to_die_ms: int | None
    after_cutoff: bool


@dataclass(frozen=True)
class MechanicRow:
    """``pull_mechanics``: every mechanic of the encounter file, hit or not."""

    report_code: str
    fight_id: int
    mechanic_id: str
    name: str | None
    ref: str
    clusters: int
    hits: int
    failures: int
    damage_taken: int
    deaths_in_window: int


@dataclass(frozen=True)
class CooldownRow:
    """``pull_cooldowns``: every cooldown cast of every category."""

    report_code: str
    fight_id: int
    ref: str
    spell_id: int
    spell_name: str
    category: str
    caster: str
    t_ms: int
    phase_id: str | None


@dataclass(frozen=True)
class WindowRow:
    """``pull_windows``: every damage-window occurrence and whether defensive cooldowns covered it."""

    report_code: str
    fight_id: int
    window_id: str
    occurrence: int
    name: str | None
    ref: str
    t_ms: int
    covered: bool
    defensive_cooldowns_active: list[int]
    defensive_cooldowns_late: int


@dataclass(frozen=True)
class ReadModelRows:
    """Every row one pull projects to."""

    pull: PullRow
    deaths: list[DeathRow]
    mechanics: list[MechanicRow]
    cooldowns: list[CooldownRow]
    windows: list[WindowRow]


def columns(row_type: type) -> list[str]:
    """A row type's column names, in field order."""
    return [f.name for f in dataclasses.fields(row_type)]


# ---------------------------------------------------------------------------
# Projection
# ---------------------------------------------------------------------------


def report_row(code: str, meta: dict[str, Any] | None, *, fetched_at: datetime | None = None) -> ReportRow:
    """
    The ``reports`` row for ``code`` from its cached ``report.json`` (``meta``; None when there is none).

    The title is kept only when it is a string, as the API reads it. The report query fetches no guild or
    visibility yet, so those columns stay empty.
    """
    meta = meta or {}
    title = meta.get("title")
    start = meta.get("startTime")
    fetched_with = meta.get(FETCHED_WITH_KEY)
    return ReportRow(
        code=code,
        title=title if isinstance(title, str) else None,
        start_time_ms=start if isinstance(start, int) and not isinstance(start, bool) else None,
        guild_id=None,
        guild_name=None,
        visibility=None,
        fetched_with=fetched_with if fetched_with in ("user", "app") else None,
        fetched_at=fetched_at,
    )


def project(pull: Pull, records: PullRecords) -> ReadModelRows:
    """
    The rows for one pull's records. Pure: reads nothing but its arguments.

    Raises:
        ValueError: ``records`` belong to another pull.
    """
    if records.pull.pull_key != pull.pull_key:
        raise ValueError(f"records for {records.pull.pull_key} cannot be projected as {pull.pull_key}")
    code, fight = pull.report_code, pull.fight_id
    d = records.digest
    enc = records.enc
    phase_names = {p.id: p.name for p in enc.phases} if enc else {}
    mech_names = {m.id: m.name for m in enc.mechanics} if enc else {}
    window_names = {w.id: w.name for w in enc.damage_windows} if enc else {}
    inputs = d.produced_from
    rhash = records_hash(inputs)

    pull_row = PullRow(
        report_code=code,
        fight_id=fight,
        pull_key=pull.pull_key,
        encounter_id=pull.encounter_id,
        encounter_name=pull.encounter_name,
        difficulty=pull.difficulty,
        kill=pull.kill,
        start_ms=pull.start_ms,
        end_ms=pull.end_ms,
        duration_ms=pull.duration_ms,
        boss_pct=pull.boss_pct,
        boss_hp_pct=pull.boss_hp_pct,
        phase_reached=d.phase_reached,
        phase_name=phase_names.get(d.phase_reached or ""),
        has_encounter_file=enc is not None,
        coverage=d.coverage,
        cutoff_ms=d.cutoff_ms,
        deaths=len(records.deaths),
        deaths_before_cutoff=sum(1 for x in records.deaths if not x.after_cutoff),
        anomalies=list(d.anomalies),
        events_hash=inputs.get("events_hash", ""),
        encfile_hash=inputs.get("encfile_hash", ""),
        records_hash=rhash,
        records_key=join_key(RECORDS_PREFIX, code, f"{fight}__{rhash}.json"),
        records_version=int(inputs.get("records_version", "0")),
        read_model_version=READ_MODEL_VERSION,
    )
    deaths = [
        DeathRow(
            report_code=code,
            fight_id=fight,
            n=n,
            ref=x.ref,
            actor_id=x.actor_id,
            name=x.name,
            t_ms=x.t_ms,
            phase_id=x.phase_id,
            killing_ability_id=x.killing_ability_id,
            killing_ability_name=x.killing_ability_name,
            overkill=x.overkill,
            time_to_die_ms=x.time_to_die_ms,
            after_cutoff=x.after_cutoff,
        )
        for n, x in enumerate(records.deaths, start=1)
    ]
    mechanics = [
        MechanicRow(
            report_code=code,
            fight_id=fight,
            mechanic_id=m.mechanic_id,
            name=mech_names.get(m.mechanic_id),
            ref=m.ref,
            clusters=m.clusters,
            hits=m.hits,
            failures=m.failures,
            damage_taken=m.damage_taken,
            deaths_in_window=m.deaths_in_window,
        )
        for m in records.mechanics
    ]
    cooldowns = [
        CooldownRow(
            report_code=code,
            fight_id=fight,
            ref=c.ref,
            spell_id=c.spell_id,
            spell_name=c.spell_name,
            category=c.category,
            caster=c.caster,
            t_ms=c.t_ms,
            phase_id=c.phase_id,
        )
        for c in records.cooldowns
    ]
    windows = [
        WindowRow(
            report_code=code,
            fight_id=fight,
            window_id=w.window_id,
            occurrence=w.occurrence,
            name=window_names.get(w.window_id),
            ref=w.ref,
            t_ms=w.t_ms,
            covered=w.covered,
            defensive_cooldowns_active=list(w.defensive_cooldowns_active),
            defensive_cooldowns_late=len(w.defensive_cooldowns_late),
        )
        for w in records.windows
    ]
    return ReadModelRows(pull_row, deaths, mechanics, cooldowns, windows)


# ---------------------------------------------------------------------------
# Writer
# ---------------------------------------------------------------------------

_REPORT_UPDATABLE = ("title", "start_time_ms", "guild_id", "guild_name", "visibility", "fetched_with")


def _insert_sql(table: str, row_type: type) -> LiteralString:
    cols = columns(row_type)
    return cast(LiteralString, f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))})")


def _values(row: object) -> tuple[Any, ...]:
    return tuple(getattr(row, name) for name in columns(type(row)))


_CHILDREN: tuple[tuple[str, str, type], ...] = (
    ("deaths", "pull_deaths", DeathRow),
    ("mechanics", "pull_mechanics", MechanicRow),
    ("cooldowns", "pull_cooldowns", CooldownRow),
    ("windows", "pull_windows", WindowRow),
)


def _upsert_report(conn: Connection[Any], report: ReportRow, *, update: bool) -> None:
    """
    Insert the report, or bring its stored row up to date. Locks the row either way, so projections of one
    report's pulls write one at a time. ``fetched_at`` is only ever filled in, never cleared.
    """
    conn.execute(
        _insert_sql("reports", ReportRow) + " ON CONFLICT (code) DO NOTHING",
        _values(report),
    )
    stored = conn.execute(
        "SELECT title, start_time_ms, guild_id, guild_name, visibility, fetched_with, fetched_at "
        "FROM reports WHERE code = %s FOR UPDATE",
        (report.code,),
    ).fetchone()
    assert stored is not None  # inserted above or already there, and locked
    if update and tuple(stored[:-1]) != tuple(getattr(report, c) for c in _REPORT_UPDATABLE):
        conn.execute(
            "UPDATE reports SET title = %s, start_time_ms = %s, guild_id = %s, guild_name = %s, visibility = %s, "
            "fetched_with = %s WHERE code = %s",
            (*(getattr(report, c) for c in _REPORT_UPDATABLE), report.code),
        )
    if report.fetched_at is not None and stored[-1] != report.fetched_at:
        conn.execute("UPDATE reports SET fetched_at = %s WHERE code = %s", (report.fetched_at, report.code))


def write_pull(conn: Connection[Any], report: ReportRow, rows: ReadModelRows, *, update_report: bool = True) -> bool:
    """
    Replace one pull's rows inside the caller's transaction.

    The pull's rows are left as they are when they already hold this records hash at this ``READ_MODEL_VERSION``;
    otherwise every row of the pull (in all five tables) is deleted and written again.

    Args:
        conn: A connection in a transaction; the caller commits.
        report: The pull's report row, inserted if missing.
        rows: ``project``'s output for the pull.
        update_report: Overwrite a stored report row's title, start, guild, visibility and token kind with
            ``report``'s. Pass False when ``report`` came from no metadata, so empty values don't replace real ones.

    Returns:
        True if the pull's rows were written, False if they were already current.

    Raises:
        ValueError: ``rows`` belong to another report.
    """
    p = rows.pull
    if p.report_code != report.code:
        raise ValueError(f"pull {p.pull_key} is not in report {report.code}")
    _upsert_report(conn, report, update=update_report)
    current = conn.execute(
        "SELECT records_hash, read_model_version FROM pulls WHERE report_code = %s AND fight_id = %s",
        (p.report_code, p.fight_id),
    ).fetchone()
    if current is not None and tuple(current) == (p.records_hash, p.read_model_version):
        return False
    # Children go with the pull row (ON DELETE CASCADE).
    conn.execute("DELETE FROM pulls WHERE report_code = %s AND fight_id = %s", (p.report_code, p.fight_id))
    conn.execute(_insert_sql("pulls", PullRow), _values(p))
    with conn.cursor() as cur:
        for attr, table, row_type in _CHILDREN:
            children = getattr(rows, attr)
            if children:
                cur.executemany(_insert_sql(table, row_type), [_values(c) for c in children])
    return True


def project_pull(
    pool: ConnectionPool,
    records: PullRecords,
    report_meta: dict[str, Any] | None,
    *,
    fetched_at: datetime | None = None,
) -> bool:
    """
    Project one pull's records into the read-model tables, in one transaction. The entry point for callers that
    have just built or loaded a pull's records.

    Args:
        pool: Pool for a database migrated to at least ``0002_read_models``.
        records: The pull's records (``EncounterData.records``).
        report_meta: The report's cached ``report.json`` (``RawPageCache.report_meta``), or None when there is none;
            then a missing report row is created empty and an existing one is left alone.
        fetched_at: When the report was fetched, if the caller knows; fills ``reports.fetched_at``.

    Returns:
        True if the pull's rows were written, False if they already held these records.
    """
    pull = records.pull
    report = report_row(pull.report_code, report_meta, fetched_at=fetched_at)
    rows = project(pull, records)
    with pool.connection() as conn, conn.transaction():
        return write_pull(conn, report, rows, update_report=report_meta is not None)
