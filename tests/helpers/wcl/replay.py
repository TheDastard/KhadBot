"""
tests/helpers/wcl/replay.py

Pieces for the WCL replay tests: a guard that fails any connection or name lookup off this machine, a clock in seconds
tied to the budget tests' ``MovableClock``, a WCL client on a replay transport (obviously fake credentials), and job
stores with an empty raw cache, so every page an ingest needs comes through replay.
"""

from __future__ import annotations

import socket
from dataclasses import dataclass
from pathlib import Path

import pytest

from helpers.encounter.synth import (
    FIXTURES,
    SYNTH_DEFENSIVE_COOLDOWN_IDS,
    SYNTH_DEFENSIVE_COOLDOWNS_FILE,
    SYNTH_ENEMY_CAST_IDS,
    SYNTH_OFFENSIVE_COOLDOWN_IDS,
    SYNTH_OFFENSIVE_COOLDOWNS_FILE,
    SYNTH_UTILITY_COOLDOWNS_FILE,
    install_synth_encounter,
)
from helpers.jobs.budget import MovableClock
from khadbot.encounter.annotate import AnnotationCache
from khadbot.encounter.config_files import load_cooldowns
from khadbot.encounter.evidence import EncounterData
from khadbot.encounter.ingest.fetch import RawPageCache
from khadbot.encounter.ingest.recorded import RECORDED_BUFF_IDS
from khadbot.encounter.records_store import RecordsStore
from khadbot.encounter.store import ParquetEventStore
from khadbot.jobs import JobContext
from khadbot.storage import LocalBlobStore
from khadbot.wcl.client import WarcraftLogsClient
from khadbot.wcl.replay import ReplayTransport

WCL_FIXTURES = FIXTURES / "wcl"
FAKE_CLIENT_ID = "fake-client-id"
FAKE_CLIENT_SECRET = "fake-client-secret-do-not-use"
LOOPBACK = {"127.0.0.1", "::1", "localhost"}

# What ``wiring.replay_fetch_filters`` gives ingest under replay: the filters the real recordings were fetched with.
RECORDED_FILTERS = {"defensive_spell_ids": list(RECORDED_BUFF_IDS), "offensive_spell_ids": [], "enemy_cast_ids": {}}

# The filters SYNTH01's pages were recorded with: replay answers these and nothing else for it.
SYNTH_FILTERS = {
    "defensive_spell_ids": SYNTH_DEFENSIVE_COOLDOWN_IDS,
    "offensive_spell_ids": SYNTH_OFFENSIVE_COOLDOWN_IDS,
    "enemy_cast_ids": SYNTH_ENEMY_CAST_IDS,
}


def guard_network(monkeypatch: pytest.MonkeyPatch) -> list[object]:
    """
    Fail every connection and name lookup off this machine (loopback stays open: asyncio's socketpair uses it on
    Windows). Returns the list the attempts are recorded in, so a test can also assert it stayed empty.
    """
    attempts: list[object] = []
    real_connect = socket.socket.connect
    real_getaddrinfo = socket.getaddrinfo

    def connect(self, address):
        host = address[0] if isinstance(address, tuple) else address
        if host not in LOOPBACK:
            attempts.append(address)
            raise AssertionError(f"tried to connect to {address!r}")
        return real_connect(self, address)

    def getaddrinfo(host, *args, **kwargs):
        if host not in LOOPBACK and host is not None:
            attempts.append(host)
            raise AssertionError(f"tried to look up {host!r}")
        return real_getaddrinfo(host, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
    return attempts


def seconds(clock: MovableClock):
    """``clock`` as seconds, for the replay transport, so the budget and the replay counter share one time."""
    return lambda: clock().timestamp()


def replay_client(transport: ReplayTransport) -> WarcraftLogsClient:
    return WarcraftLogsClient(FAKE_CLIENT_ID, FAKE_CLIENT_SECRET, transport=transport)


@dataclass
class ReplayEnv:
    context: JobContext
    raw: RawPageCache
    events: ParquetEventStore
    records: RecordsStore


def replay_env(root: Path, client: WarcraftLogsClient, *, synthetic: bool) -> ReplayEnv:
    """
    Job stores under ``root`` with an empty raw cache and ``client`` (an entered replay client). ``synthetic`` uses
    SYNTH01's encounter file, registries and recorded filters; otherwise the authored files and
    ``RECORDED_FILTERS``, what wiring gives ingest under replay. Batching is off, as wiring turns it off under replay.
    """
    blobs = LocalBlobStore(root / "blobs")
    raw = RawPageCache(blobs)
    events = ParquetEventStore(blobs)
    records = RecordsStore(blobs)
    if synthetic:
        enc_root = root / "encounters"
        install_synth_encounter(enc_root)
        data = EncounterData(
            events,
            enc_root=enc_root,
            defensive=load_cooldowns("defensive", SYNTH_DEFENSIVE_COOLDOWNS_FILE),
            offensive=load_cooldowns("offensive", SYNTH_OFFENSIVE_COOLDOWNS_FILE),
            utility=load_cooldowns("utility", SYNTH_UTILITY_COOLDOWNS_FILE),
            cache=AnnotationCache(blobs),
            records_store=records,
        )
        filters = SYNTH_FILTERS
    else:
        data = EncounterData(events, cache=AnnotationCache(blobs), records_store=records)
        filters = RECORDED_FILTERS
    context = JobContext(
        data=data, store=events, cache=raw, client=client, filters=lambda: filters, batch=False, projector=None
    )
    return ReplayEnv(context, raw, events, records)
