"""
db/projector.py

The hosted read-model projector: the ``ReadModelProjector`` that ``ProcessPull`` calls after it stores a pull's
records. It writes the pull's rows through ``read_models.project_pull``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from khadbot.db.connect import get_pool
from khadbot.db.read_models import project_pull
from khadbot.encounter.digest import PullRecords
from khadbot.encounter.ingest.fetch import RawPageCache

# (pool, records, report metadata) -> whether rows were written; ``read_models.project_pull``.
ReadModelWriter = Callable[[Any, PullRecords, dict | None], bool]


class PostgresReadModelProjector:
    """
    Projects a pull's records into the Postgres read-model tables (``project_pull``), with the report's cached
    metadata from ``raw``. Idempotent because the writer is: the same records twice write nothing. The pool and the
    raw cache (``raw_factory``, when ``raw`` is not given) are taken on first use, so building a projector opens no
    connection.
    """

    def __init__(
        self,
        raw: RawPageCache | None = None,
        *,
        raw_factory: Callable[[], RawPageCache] | None = None,
        pool: Callable[[], Any] | None = None,
        write: ReadModelWriter | None = None,
    ) -> None:
        self._raw = raw
        self._raw_factory = raw_factory
        self._pool = pool or get_pool
        self._write = write or project_pull

    async def project(self, records: PullRecords) -> None:
        if self._raw is None:
            if self._raw_factory is None:
                raise ValueError("PostgresReadModelProjector needs a raw cache or a raw_factory")
            self._raw = self._raw_factory()
        raw, pool, write = self._raw, self._pool, self._write
        meta = await asyncio.to_thread(raw.report_meta, records.pull.report_code)
        await asyncio.to_thread(write, pool(), records, meta)


__all__ = ["PostgresReadModelProjector", "ReadModelWriter"]
