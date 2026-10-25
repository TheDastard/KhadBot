"""
scripts/dev/queue_smoke.py

Smoke test for the stack's job queue. Run it inside the stack's image (``mise run stack:queue-smoke``), where it finds
Postgres through the same environment as the worker. It queues an offline ingest of a report that is in no cache, waits
for the worker to pick it up, and checks the job ends ``failed`` with the typed code ``not_in_cache``: that proves
enqueue, the queue tables, the running worker, the ``jobs`` row and typed failures on the real stack without WCL.

    docker compose -f deploy/compose.yaml --profile smoke run --rm queue-smoke [--timeout 60]

An ingest of a fixture report through the worker is covered by the hosted tests; the stack's worker uses the
configured filters and encounter files, which the synthetic fixture does not match.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time

REPORT = "NOSUCH01"
EXPECTED_CODE = "not_in_cache"


async def smoke(timeout: float) -> str | None:
    """Queue the job and wait for it. Returns a problem description, or None when the queue behaved."""
    from khadbot.jobs import IngestReport
    from khadbot.jobs.queue import build_queue

    queue = build_queue()
    async with queue.open():
        enqueued = await queue.enqueue(IngestReport(code=REPORT, offline=True))
        print(f"queued job {enqueued.job.id} (created={enqueued.created})")
        again = await queue.enqueue(IngestReport(code=REPORT, offline=True))
        if again.job.id != enqueued.job.id and again.created:
            return "a second request for the same report started another ingest"
        deadline = time.monotonic() + timeout
        while True:
            job = await queue.store.get(enqueued.job.id)
            if job is not None and not job.active:
                break
            if time.monotonic() >= deadline:
                state = job.state.value if job else "missing"
                return f"the job is still {state} after {timeout:.0f}s: is the worker running?"
            await asyncio.sleep(1)
    print(f"job ended {job.state.value} ({job.error_code}) after {job.attempts} attempt(s)")
    if job.state.value != "failed" or job.error_code != EXPECTED_CODE:
        return f"expected a failed job with code {EXPECTED_CODE}, got {job.state.value} / {job.error_code}"
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--timeout", type=float, default=60, help="Seconds to wait for the worker (default: 60)")
    args = parser.parse_args()
    problem = asyncio.run(smoke(args.timeout), loop_factory=asyncio.SelectorEventLoop)
    if problem:
        sys.exit(problem)
    print("queue: ok")


if __name__ == "__main__":
    main()
