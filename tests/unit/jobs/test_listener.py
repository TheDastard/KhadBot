"""
tests/unit/jobs/test_listener.py

``SharedListener``: many readers share one subscription to the source, which is opened for the first reader and closed
after the last; a source that ends or fails ends every reader's iterator.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest

from khadbot.jobs.listener import QUEUE_SIZE, SharedListener


class FakeSource:
    """A notification source that counts how often it is opened and closed."""

    def __init__(self, *, fail: Exception | None = None) -> None:
        self.opened = 0
        self.closed = 0
        self.fail = fail
        self.feed: asyncio.Queue[str | None] = asyncio.Queue()

    @asynccontextmanager
    async def __call__(self) -> AsyncIterator[AsyncIterator[str]]:
        if self.fail is not None:
            raise self.fail
        self.opened += 1

        async def events() -> AsyncIterator[str]:
            while (payload := await self.feed.get()) is not None:
                yield payload

        try:
            yield events()
        finally:
            self.closed += 1


async def test_many_readers_share_one_subscription():
    source = FakeSource()
    listener = SharedListener(source)
    got: list[list[str]] = []
    listening = 0

    async def reader() -> None:
        nonlocal listening
        async with listener.subscribe() as events:
            listening += 1
            got.append([await anext(events), await anext(events)])

    readers = [asyncio.create_task(reader()) for _ in range(50)]
    while listening < 50:
        await asyncio.sleep(0)
    assert source.opened == 1  # fifty readers, one connection
    source.feed.put_nowait("job-a")
    source.feed.put_nowait("job-b")
    await asyncio.gather(*readers)
    assert got == [["job-a", "job-b"]] * 50
    assert (source.opened, source.closed, listener.readers) == (1, 1, 0)  # closed after the last reader left


async def test_the_source_is_opened_again_for_a_later_reader():
    source = FakeSource()
    listener = SharedListener(source)
    for expected in (1, 2):
        async with listener.subscribe() as events:
            source.feed.put_nowait("job-a")
            assert await anext(events) == "job-a"
        assert (source.opened, source.closed) == (expected, expected)


async def test_a_reader_gets_only_what_is_sent_after_it_subscribed():
    source = FakeSource()
    listener = SharedListener(source)
    async with listener.subscribe() as first:
        source.feed.put_nowait("before")
        assert await anext(first) == "before"
        async with listener.subscribe() as second:
            source.feed.put_nowait("after")
            assert await anext(second) == "after"
            assert await anext(first) == "after"


async def test_a_source_that_ends_ends_every_reader():
    source = FakeSource()
    listener = SharedListener(source)
    async with listener.subscribe() as first, listener.subscribe() as second:
        source.feed.put_nowait(None)  # the connection was lost
        for events in (first, second):
            with pytest.raises(StopAsyncIteration):
                await anext(events)


async def test_a_source_that_cannot_open_raises_to_the_reader():
    listener = SharedListener(FakeSource(fail=OSError("no database")))
    with pytest.raises(OSError, match="no database"):
        async with listener.subscribe():
            pass
    assert listener.readers == 0


async def test_a_reader_that_falls_behind_misses_notifications_but_still_ends():
    source = FakeSource()
    listener = SharedListener(source)
    async with listener.subscribe() as events:
        for i in range(QUEUE_SIZE + 10):
            source.feed.put_nowait(f"job-{i}")
        source.feed.put_nowait(None)
        seen = [payload async for payload in events]
    assert 0 < len(seen) < QUEUE_SIZE + 10  # some were dropped, and the end still arrived
