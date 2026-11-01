"""
jobs/progress.py

How a job reports where it is: ``(stage, pull, fraction)`` events through the ``Progress`` protocol. The queue
turns them into stored job state; the CLI prints them as lines. A job-level event has ``pull=None``.

An ingest also describes the pulls it fetches once it knows them (``PullInfo``: encounter and difficulty), through
the optional ``PullDescriber`` protocol, so a client can name the pull before any of its pages are fetched.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict

# ---------------------------------------------------------------------------
# Pull description
# ---------------------------------------------------------------------------


class PullInfo(BaseModel):
    """What a pull is: its key, the encounter and the WCL difficulty code, as the report's metadata names them."""

    model_config = ConfigDict(frozen=True)

    pull_key: str
    encounter_id: int
    encounter_name: str
    difficulty: int | None = None


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


class Progress(Protocol):
    """Receives progress events. ``report`` is called from the job's event loop and must not block."""

    def report(self, stage: str, pull: str | None, fraction: float) -> None: ...


@runtime_checkable
class PullDescriber(Protocol):
    """A ``Progress`` that also wants to know which pulls an ingest fetches. Called once, before the first page."""

    def describe(self, pulls: Sequence[PullInfo]) -> None: ...


class NullProgress:
    """Drops every event."""

    def report(self, stage: str, pull: str | None, fraction: float) -> None:
        return None


@dataclass
class RecordingProgress:
    """Keeps every event, in order. For tests and for callers that summarize a run afterwards."""

    events: list[tuple[str, str | None, float]] = field(default_factory=list)
    described: list[PullInfo] = field(default_factory=list)

    def report(self, stage: str, pull: str | None, fraction: float) -> None:
        self.events.append((stage, pull, fraction))

    def describe(self, pulls: Sequence[PullInfo]) -> None:
        self.described.extend(pulls)


@dataclass(frozen=True)
class LineProgress:
    """
    Prints one ``<pull>  <stage>  <percent>`` line per pull event through ``emit`` (the CLI's styled print).
    Job-level events, which name no pull, print nothing.
    """

    emit: Callable[[str], None]

    def report(self, stage: str, pull: str | None, fraction: float) -> None:
        if pull is not None:
            self.emit(f"{pull}  {stage}  {fraction:.0%}")
