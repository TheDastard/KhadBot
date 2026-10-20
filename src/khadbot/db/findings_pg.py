"""
db/findings_pg.py

Postgres findings store for hosted mode: the same methods as ``SqliteFindingsStore``, over the ``findings``
table from ``0001_findings.sql``. The report is stored as ``jsonb``.
"""

from __future__ import annotations

from psycopg_pool import ConnectionPool

from khadbot.encounter.models import PullFindings

# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


class PostgresFindingsStore:
    """Per-pull findings in Postgres. Safe to share between threads: each call takes a pooled connection."""

    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    def get(self, key: str) -> PullFindings | None:
        """The findings stored under ``key``, flagged or not, or None."""
        with self._pool.connection() as conn:
            row = conn.execute("SELECT report::text, flagged FROM findings WHERE cache_key = %s", (key,)).fetchone()
        return _load(*row) if row else None

    def put(self, pf: PullFindings) -> None:
        """Store ``pf`` under its cache key, replacing any earlier row."""
        with self._pool.connection() as conn:
            conn.execute(
                """
                INSERT INTO findings (cache_key, pull_key, report, flagged, created_at)
                VALUES (%s, %s, %s::jsonb, %s, %s)
                ON CONFLICT (cache_key) DO UPDATE
                SET pull_key = EXCLUDED.pull_key,
                    report = EXCLUDED.report,
                    flagged = EXCLUDED.flagged,
                    created_at = EXCLUDED.created_at
                """,
                (pf.cache_key, pf.pull_key, pf.model_dump_json(), pf.flagged, pf.created_at),
            )

    def for_pull(self, pull_key: str, *, include_flagged: bool = False) -> list[PullFindings]:
        """Stored findings for a pull, newest first. Flagged rows are left out unless asked for."""
        sql = "SELECT report::text, flagged FROM findings WHERE pull_key = %s"
        if not include_flagged:
            sql += " AND NOT flagged"
        sql += " ORDER BY created_at DESC"
        with self._pool.connection() as conn:
            rows = conn.execute(sql, (pull_key,)).fetchall()
        return [_load(*r) for r in rows]

    def flag(self, finding_id: str) -> bool:
        """Flag the stored findings that contain ``finding_id``. Returns False if none do."""
        with self._pool.connection() as conn:
            cur = conn.execute(
                """
                UPDATE findings SET flagged = true
                WHERE report -> 'findings' @> jsonb_build_array(jsonb_build_object('id', %s::text))
                """,
                (finding_id,),
            )
            return cur.rowcount > 0


def _load(raw: str, flagged: bool) -> PullFindings:
    return PullFindings.model_validate_json(raw).model_copy(update={"flagged": flagged})
