"""
tests/unit/encounter/test_positions.py

Position lookups (analysis/positions.py) on small hand-written tables: an exact-time hit, the latest earlier
sample and its age, the age limit, the instance filter, several actor IDs, each unit's latest sample across a
map change, and distances across maps.
"""

from __future__ import annotations

import pytest
from helpers.encounter.events import make_positions

from khadbot.encounter.analysis.positions import Sample, distance_yd, latest_per_unit, position_at
from khadbot.encounter.store import empty_positions

TABLE = make_positions(
    [
        (1_000, 1, 0, 0),
        (2_000, 1, 300, 400),
        (2_000, 11, 1_000, 0, 1),  # two NPCs sharing actor 11, told apart by instance
        (2_500, 11, 2_000, 0, 2),
        (3_000, 12, 50, 50, None, 2),  # another map
    ]
)


def test_exact_time_hit_has_age_zero():
    s = position_at(TABLE, [1], 2_000, max_age_ms=0)
    assert s == Sample(t_ms=2_000, actor_id=1, instance=None, x=300, y=400, map_id=1, age_ms=0)


def test_latest_earlier_sample_and_its_age():
    s = position_at(TABLE, [1], 2_400, max_age_ms=1_000)
    assert (s.t_ms, s.age_ms) == (2_000, 400)
    assert position_at(TABLE, [1], 999, max_age_ms=None) is None  # nothing at or before


def test_age_limit():
    assert position_at(TABLE, [1], 3_500, max_age_ms=1_500).age_ms == 1_500
    assert position_at(TABLE, [1], 3_501, max_age_ms=1_500) is None
    assert position_at(TABLE, [1], 60_000, max_age_ms=None).t_ms == 2_000  # no limit


def test_instance_filter_and_several_actor_ids():
    assert position_at(TABLE, [11], 3_000, max_age_ms=None).instance == 2  # latest of either instance
    assert position_at(TABLE, [11], 3_000, instance=1, max_age_ms=None).x == 1_000
    assert position_at(TABLE, [11], 3_000, instance=3, max_age_ms=None) is None
    assert position_at(TABLE, [1, 11], 2_200, max_age_ms=None).actor_id == 11  # the later of the two actors


def test_latest_per_unit_keeps_each_units_latest_sample_oldest_first():
    got = latest_per_unit(TABLE, [1, 11, 12], 3_000, max_age_ms=None)
    assert [(s.actor_id, s.instance, s.t_ms, s.age_ms) for s in got] == [
        (1, None, 2_000, 1_000),
        (11, 1, 2_000, 1_000),
        (11, 2, 2_500, 500),
        (12, None, 3_000, 0),
    ]
    assert [s.actor_id for s in latest_per_unit(TABLE, [1, 11], 3_000, max_age_ms=600)] == [11]
    assert latest_per_unit(TABLE, [], 3_000, max_age_ms=None) == []


def test_latest_per_unit_after_a_map_change_shows_only_the_new_map():
    moved = make_positions([(1_000, 11, 0, 0, None, 1), (2_000, 11, 5_000, 5_000, None, 2)])
    assert [(s.map_id, s.x) for s in latest_per_unit(moved, [11], 3_000, max_age_ms=None)] == [(2, 5_000)]
    assert [(s.map_id, s.x) for s in latest_per_unit(moved, [11], 1_500, max_age_ms=None)] == [(1, 0)]


def test_empty_table():
    assert position_at(empty_positions(), [1], 1_000, max_age_ms=None) is None
    assert latest_per_unit(empty_positions(), [1], 1_000, max_age_ms=None) == []


def test_distance_in_yards_and_map_mismatch():
    a = position_at(TABLE, [1], 1_000, max_age_ms=0)
    b = position_at(TABLE, [1], 2_000, max_age_ms=0)
    assert distance_yd(a, b) == pytest.approx(5.0)  # 300, 400 units: 500 units = 5 yards
    other_map = position_at(TABLE, [12], 3_000, max_age_ms=0)
    assert distance_yd(a, other_map) is None
