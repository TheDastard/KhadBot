"""
jobs/progress.py

How a job reports where it is: ``(stage, pull, fraction)`` events through the ``Progress`` protocol. The queue
turns them into stored job state; the CLI prints them as lines. A job-level event has ``pull=None``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


class Progress(Protocol):
    """Receives progress events. ``report`` is called from the job's event loop and must not block."""

    def report(self, stage: str, pull: str | None, fraction: float) -> None: ...


class NullProgress:
    """Drops every event."""

    def report(self, stage: str, pull: str | None, fraction: float) -> None:
        return None


@dataclass
class RecordingProgress:
    """Keeps every event, in order. For tests and for callers that summarize a run afterwards."""

    events: list[tuple[str, str | None, float]] = field(default_factory=list)

    def report(self, stage: str, pull: str | None, fraction: float) -> None:
        self.events.append((stage, pull, fraction))


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
