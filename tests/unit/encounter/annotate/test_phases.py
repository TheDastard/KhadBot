"""
tests/unit/encounter/annotate/test_phases.py

Phase detection: each trigger type, ordering, an unmatched trigger, and SYNTH01; detected phases checked
against WCL's phase transitions.
"""

from __future__ import annotations

import pytest
from helpers.encounter.events import BOSS, make_events, make_pull

from khadbot.encounter.annotate.phases import boss_ids_of, detect_phases, unmatched_phase, unmatched_phases
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


# ===========================================================================
# unmatched_phases: detected phases against WCL's transitions
# ===========================================================================


def _unmatched(enc, transitions):
    pull = make_pull(phase_transitions=transitions)
    return unmatched_phases(pull, detect_phases(pull, EVENTS, enc, {BOSS}), enc)


def test_wcl_phase_trigger_seen_in_transitions_but_not_detected():
    # WCL phase 2 started at 5 s, before p2 (the boss cast at 10 s); p3 searches only after 10 s and misses it.
    enc = enc_with({"trigger": "boss_cast", "ability_id": 500}, {"trigger": "wcl_phase", "phase": 2})
    assert _unmatched(enc, [(1, 0), (2, 5_000)]) == ["p3"]


def test_wcl_phase_trigger_absent_from_transitions_is_not_flagged():
    # An early wipe that never reached WCL phase 3, even though WCL counts more transitions than detected phases.
    enc = enc_with({"trigger": "wcl_phase", "phase": 3})
    assert _unmatched(enc, [(1, 0), (2, 20_000)]) == []


def test_other_trigger_flagged_when_wcl_saw_more_phases():
    enc = enc_with({"trigger": "boss_cast", "ability_id": 999})  # never cast
    assert _unmatched(enc, [(1, 0), (2, 20_000)]) == ["p2"]


def test_other_trigger_not_flagged_when_counts_match():
    enc = enc_with({"trigger": "boss_cast", "ability_id": 999})
    assert _unmatched(enc, [(1, 0)]) == []
    assert _unmatched(enc, []) == []  # no transitions from WCL at all (some Mythic pulls)


def test_count_rule_flags_only_as_many_phases_as_wcl_saw():
    enc = enc_with(*({"trigger": "boss_cast", "ability_id": 999} for _ in range(3)))  # p2..p4 never cast
    assert _unmatched(enc, [(1, 0), (2, 20_000), (3, 30_000)]) == ["p2", "p3"]


def test_repeated_transition_ids_count_once():
    enc = enc_with({"trigger": "boss_cast", "ability_id": 999})
    assert _unmatched(enc, [(1, 0), (1, 20_000)]) == []


def test_every_phase_detected():
    enc = enc_with({"trigger": "boss_cast", "ability_id": 500})
    assert _unmatched(enc, [(1, 0), (2, 10_000)]) == []


# ===========================================================================
# Phases WCL repeats: listed once per repeat, or a repeating block
# ===========================================================================

STAGE, BREAK = {"trigger": "wcl_phase", "phase": 1}, {"trigger": "wcl_phase", "phase": 2}
# An alternating fight: stage (WCL 1) from the pull, intermissions (WCL 2) at 20 s and 50 s, stages again after.
ALTERNATING = [(1, 0), (2, 20_000), (1, 30_000), (2, 50_000), (1, 55_000)]


def enc_phases(*phases: tuple[str, dict, bool]):
    rows = [{"id": "p1", "name": "P1", "start": {"trigger": "pull"}}]
    rows += [{"id": pid, "name": pid, "start": t, **({"repeat": True} if r else {})} for pid, t, r in phases]
    return parse_encounter(
        {
            "encounter_id": 1,
            "name": "X",
            "zone": {"id": 1, "name": "Z"},
            "file_version": 1,
            "phases": [{**p, "difficulties": ["heroic"]} for p in rows],
        }
    )


LISTED = enc_phases(
    ("int_1", BREAK, False),
    ("p1_2", STAGE, False),
    ("int_2", BREAK, False),
    ("p1_3", STAGE, False),
    ("int_3", BREAK, False),
    ("p1_4", STAGE, False),
)
REPEATING = enc_phases(("intermission", BREAK, True), ("stage", STAGE, True))


def _spans(enc, transitions):
    pull = make_pull(phase_transitions=transitions)
    spans = detect_phases(pull, EVENTS, enc, {BOSS})
    return pull, spans


def test_listed_repeats_not_reached_are_not_flagged():
    # The pull ends in the third stage; the third intermission and fourth stage were never reached.
    pull, spans = _spans(LISTED, ALTERNATING)
    assert [s.phase_id for s in spans] == ["p1", "int_1", "p1_2", "int_2", "p1_3"]
    assert unmatched_phase(spans, LISTED) == "int_3"
    assert unmatched_phases(pull, spans, LISTED) == []


def test_listed_repeat_reached_but_not_detected_is_flagged():
    # The second intermission's trigger is a boss cast that never happens, so detection stops after p1_2,
    # though WCL reports a second intermission and a third stage.
    enc = enc_phases(
        ("int_1", BREAK, False),
        ("p1_2", STAGE, False),
        ("int_2", {"trigger": "boss_cast", "ability_id": 999}, False),
        ("p1_3", STAGE, False),
        ("int_3", BREAK, False),
    )
    pull, spans = _spans(enc, ALTERNATING)
    assert [s.phase_id for s in spans] == ["p1", "int_1", "p1_2"]
    # p1_3 is the second listed WCL-1 phase and WCL 1 recurs twice after the pull: reached. int_3 is the second
    # listed WCL-2 phase and WCL 2 occurs twice: reached. int_2 has another trigger; WCL saw fewer distinct IDs.
    assert unmatched_phases(pull, spans, enc) == ["p1_3", "int_3"]


def test_opening_transition_is_the_pull_phase():
    # A transition at 0 ms is the pull start, not a repeat of the first stage.
    pull, spans = _spans(LISTED, [(1, 0)])
    assert unmatched_phases(pull, spans, LISTED) == []


def test_repeating_block_gives_a_span_per_repeat():
    pull, spans = _spans(REPEATING, ALTERNATING)
    assert [(s.phase_id, s.start_ms, s.end_ms) for s in spans] == [
        ("p1", 0, 20_000),
        ("intermission", 20_000, 30_000),
        ("stage", 30_000, 50_000),
        ("intermission", 50_000, 55_000),
        ("stage", 55_000, 60_000),
    ]
    assert unmatched_phase(spans, REPEATING) == "intermission"  # next in line
    assert unmatched_phases(pull, spans, REPEATING) == []


def test_repeating_block_after_listed_phases():
    enc = enc_phases(("first_break", BREAK, False), ("stage", STAGE, True), ("intermission", BREAK, True))
    pull, spans = _spans(enc, ALTERNATING)
    assert [s.phase_id for s in spans] == ["p1", "first_break", "stage", "intermission", "stage"]
    assert unmatched_phases(pull, spans, enc) == []


def test_repeating_block_with_boss_casts():
    enc = enc_phases(("cast_phase", {"trigger": "boss_cast", "ability_id": 500}, True))
    _, spans = _spans(enc, [])
    assert [(s.phase_id, s.start_ms) for s in spans] == [("p1", 0), ("cast_phase", 10_000), ("cast_phase", 30_000)]


def test_repeating_phase_flagged_once_when_wcl_saw_more():
    # The stage's repeat is a boss cast that never comes, so WCL's later intermission is missed.
    enc = enc_phases(("intermission", BREAK, True), ("stage", {"trigger": "boss_cast", "ability_id": 999}, True))
    pull, spans = _spans(enc, ALTERNATING)
    assert [s.phase_id for s in spans] == ["p1", "intermission"]
    assert unmatched_phases(pull, spans, enc) == ["intermission"]
