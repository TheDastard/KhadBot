"""
tests/unit/encounter/annotate/test_phases.py

Phase detection: each trigger type, ordering, an unmatched trigger, and SYNTH01.
"""

from __future__ import annotations

import pytest
from helpers.encounter.events import BOSS, make_events, make_pull

from khadbot.encounter.annotate.phases import boss_ids_of, detect_phases, unmatched_phase
from khadbot.encounter.config_files import parse_encounter


def enc_with(*triggers: dict):
    phases = [{"id": "p1", "name": "P1", "start": {"trigger": "pull"}}]
    phases += [{"id": f"p{i + 2}", "name": f"P{i + 2}", "start": t} for i, t in enumerate(triggers)]
    phases = [{**p, "difficulties": ["heroic"]} for p in phases]
    return parse_encounter(
        {"encounter_id": 1, "name": "X", "zone": {"id": 1, "name": "Z"}, "file_version": 1, "phases": phases}
    )


EVENTS = make_events(
    [
        {"t_ms": 5_000, "type": "begincast", "source_id": BOSS, "ability_id": 500},  # begincast does not count
        {"t_ms": 6_000, "type": "cast", "source_id": 11, "ability_id": 500},  # not the boss
        {"t_ms": 10_000, "type": "cast", "source_id": BOSS, "ability_id": 500},
        {"t_ms": 12_000, "type": "applybuff", "source_id": BOSS, "target_id": BOSS, "ability_id": 600},
        {"t_ms": 15_000, "type": "damage", "source_id": 1, "target_id": BOSS, "ability_id": 1, "hp_pct": 71.0},
        {"t_ms": 16_000, "type": "damage", "source_id": 1, "target_id": BOSS, "ability_id": 1, "hp_pct": 69.0},
        {"t_ms": 30_000, "type": "cast", "source_id": BOSS, "ability_id": 500},
    ]
)


@pytest.mark.parametrize(
    ("trigger", "start"),
    [
        ({"trigger": "boss_cast", "ability_id": 500}, 10_000),
        ({"trigger": "boss_aura_applied", "ability_id": 600}, 12_000),
        ({"trigger": "boss_hp_below", "pct": 70}, 16_000),
        ({"trigger": "time", "t_ms": 25_000}, 25_000),
        ({"trigger": "wcl_phase", "phase": 2}, 20_000),
    ],
)
def test_each_trigger(trigger, start):
    pull = make_pull(phase_transitions=[(1, 0), (2, 20_000)])
    spans = detect_phases(pull, EVENTS, enc_with(trigger), {BOSS})
    assert [(s.phase_id, s.start_ms, s.end_ms) for s in spans] == [("p1", 0, start), ("p2", start, 60_000)]


def test_triggers_searched_after_previous_start():
    # p2 at the first boss cast (10 s); p3 needs another cast of the same ability *after* 10 s.
    enc = enc_with({"trigger": "boss_cast", "ability_id": 500}, {"trigger": "boss_cast", "ability_id": 500})
    spans = detect_phases(make_pull(), EVENTS, enc, {BOSS})
    assert [s.start_ms for s in spans] == [0, 10_000, 30_000]
    assert unmatched_phase(spans, enc) is None


def test_unmatched_trigger_stops_detection():
    enc = enc_with(
        {"trigger": "boss_cast", "ability_id": 999},  # never cast
        {"trigger": "boss_cast", "ability_id": 500},  # would match, but detection already stopped
    )
    spans = detect_phases(make_pull(), EVENTS, enc, {BOSS})
    assert [s.phase_id for s in spans] == ["p1"]
    assert spans[0].end_ms == 60_000
    assert unmatched_phase(spans, enc) == "p2"


def test_time_after_pull_end_is_unmatched():
    spans = detect_phases(make_pull(), EVENTS, enc_with({"trigger": "time", "t_ms": 90_000}), {BOSS})
    assert len(spans) == 1


def test_no_boss_ids_never_matches_boss_triggers():
    spans = detect_phases(make_pull(), EVENTS, enc_with({"trigger": "boss_cast", "ability_id": 500}), set())
    assert len(spans) == 1


@pytest.mark.parametrize(("fight", "p2_start", "duration"), [(1, 35_000, 60_000), (2, 30_000, 80_000)])
def test_synth_reaches_p2(synth_ingested, synth_enc, fight, p2_start, duration):
    pull = next(p for p in synth_ingested.pulls("SYNTH01") if p.fight_id == fight)
    boss_ids = boss_ids_of(synth_ingested.actors("SYNTH01"))
    spans = detect_phases(pull, synth_ingested.events(pull.pull_key), synth_enc, boss_ids)
    assert [(s.phase_id, s.start_ms, s.end_ms) for s in spans] == [
        ("p1", 0, p2_start),
        ("p2", p2_start, duration),
    ]
