"""
tests/helpers/jobs/ingest.py

What a finished ingest leaves in the stores, through the job path: the ingest job (raw pages and manifests), then
each ready pull's events normalized into the event store, the first half of a process job. Records are not built.
"""

from __future__ import annotations

from khadbot.encounter.ingest import process_pull_events
from khadbot.encounter.models import IngestResult
from khadbot.jobs import IngestReport, JobContext, run_ingest_report


async def fetch_and_normalize(spec: IngestReport, ctx: JobContext) -> IngestResult:
    """Run the ingest job for ``spec``, then normalize every pull it left ready into ``ctx``'s event store."""
    result = await run_ingest_report(spec, ctx)
    for key in [*result.pulls, *result.skipped]:
        process_pull_events(ctx.event_store(), ctx.raw_cache(), key)
    return result
