"""
jobs/listener.py

``SharedListener``: one subscription to a notification source, shared by every reader in the process.

The hosted job store announces each change with Postgres ``NOTIFY``, and a ``LISTEN`` needs a connection of its own
for as long as it listens. One connection per reader (a job event stream) would let anyone who can open streams use up
the database's connections, so the store opens one and hands every reader a queue fed from it. The source is opened
when the first reader subscribes and closed when the last one leaves.

A reader's iterator ends when the source ends or fails; the reader then starts over (a job event stream closes and its
client reconnects, getting the current state first). A reader that falls ``QUEUE_SIZE`` notifications behind misses
the ones that don't fit; readers re-read their job on a timer, so a missed notification only delays them.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager

logger = logging.getLogger(__name__)

QUEUE_SIZE = 1024  # notifications one reader may have waiting

Source = Callable[[], AbstractAsyncContextManager[AsyncIterator[str]]]
"""Opens the subscription: ``async with source() as events`` yields payloads until it is closed."""

# ---------------------------------------------------------------------------
# Listener
# ---------------------------------------------------------------------------


class SharedListener:
    """
    Fans one notification source out to any number of readers.

    Args:
        source: Opens the underlying subscription (hosted: a ``LISTEN`` on one Postgres connection).
    """

    def __init__(self, source: Source) -> None:
        self._source = source
        self._readers: set[asyncio.Queue[str | None]] = set()
        self._task: asyncio.Task[None] | None = None
        self._ready: asyncio.Event | None = None
        self._error: Exception | None = None

    @property
    def readers(self) -> int:
        """How many readers are subscribed now."""
        return len(self._readers)

    @asynccontextmanager
    async def subscribe(self) -> AsyncIterator[AsyncIterator[str]]:
        """
        ``async with listener.subscribe() as events``: payloads sent after the block is entered, in order.

        Raises:
            Exception: Whatever the source raised when it could not be opened.
        """
        queue: asyncio.Queue[str | None] = asyncio.Queue(maxsize=QUEUE_SIZE)
        self._readers.add(queue)
        try:
            await self._start()
            yield _drain(queue)
        finally:
            self._readers.discard(queue)
            if not self._readers:
                await self._stop()

    # ------------------------------------------------------------------

    async def _start(self) -> None:
        """Make sure the source is open and listening before the caller reads anything."""
        task, ready = self._task, self._ready
        if task is None or ready is None or task.done() or task.get_loop() is not asyncio.get_running_loop():
            ready = self._ready = asyncio.Event()
            self._error = None
            task = self._task = asyncio.create_task(self._pump(ready))
        waiter = asyncio.ensure_future(ready.wait())
        try:
            await asyncio.wait({waiter, task}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            waiter.cancel()
        if not ready.is_set():
            raise self._error or ConnectionError("The notification source closed before it was listening.")

    async def _stop(self) -> None:
        task, self._task, self._ready = self._task, None, None
        if task is not None and not task.done() and task.get_loop() is asyncio.get_running_loop():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task

    async def _pump(self, ready: asyncio.Event) -> None:
        try:
            async with self._source() as events:
                ready.set()
                async for payload in events:
                    for queue in list(self._readers):
                        with contextlib.suppress(asyncio.QueueFull):
                            queue.put_nowait(payload)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - the readers are told the subscription ended and start over
            self._error = exc
            logger.warning("The shared notification subscription failed: %s", type(exc).__name__)
        finally:
            for queue in list(self._readers):
                _end(queue)


def _end(queue: asyncio.Queue[str | None]) -> None:
    """Tell a reader the subscription is over, making room for the marker if its queue is full."""
    while True:
        try:
            queue.put_nowait(None)
            return
        except asyncio.QueueFull:
            with contextlib.suppress(asyncio.QueueEmpty):
                queue.get_nowait()


async def _drain(queue: asyncio.Queue[str | None]) -> AsyncIterator[str]:
    while True:
        payload = await queue.get()
        if payload is None:
            return
        yield payload


__all__ = ["QUEUE_SIZE", "SharedListener", "Source"]
