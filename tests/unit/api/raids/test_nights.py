"""
tests/unit/api/raids/test_nights.py

The pieces that narrow a character's nights: matching player actors by name, and keeping only some pulls of a night
with each group's best pull recomputed.
"""

from __future__ import annotations

from helpers.api.search import NOW

from khadbot.api.raids.nights import character_actor_ids, only_pulls_of
from khadbot.api.schemas import LookupGroup, LookupPull, ReportLookup


def test_actors_match_by_name_in_any_case_and_only_players():
    meta = {
        "masterData": {
            "actors": [
                {"id": 1, "name": "Dastard", "type": "Player"},
                {"id": 2, "name": "dastard", "type": "Pet"},
                {"id": 3, "name": "DASTARD", "type": "Player"},
            ]
        }
    }
    assert character_actor_ids(meta, "dastard") == {1, 3}


def test_only_pulls_of_recomputes_the_best_pull():
    def pull(fight, outcome, pct):
        return LookupPull(
            fight=fight, ordinal=fight, label=f"P{fight}", outcome=outcome, duration_ms=1, boss_pct=pct,
            boss_hp_pct=None, ready=False,
        )  # fmt: skip

    night = ReportLookup(
        code="X",
        title=None,
        started_at=NOW,
        zone=None,
        focus_fight=None,
        groups=[
            LookupGroup(
                encounter_id=1,
                encounter_name="Boss",
                difficulty=None,
                difficulty_name=None,
                pulls=[pull(1, "wipe", 40.0), pull(2, "wipe", 10.0), pull(3, "kill", 0.0)],
                best_fight=3,
            )
        ],
    )
    narrowed = only_pulls_of(night, {1, 2}, None)
    assert narrowed.groups[0].best_fight == 2
    assert [p.label for p in narrowed.groups[0].pulls] == ["P1", "P2"]
    assert only_pulls_of(night, {1}, 2).groups == []
