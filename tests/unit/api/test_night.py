"""
tests/unit/api/test_night.py

The night summary checked against values computed here, independently, from ``build_pull_records``; and every
briefing line checked with the analyst's own verify step (``check_finding``), so each number it states appears in
the records its refs resolve to. Nothing is hard-coded: ingest and analysis changes move the numbers, not the tests.
"""

from __future__ import annotations

from collections import Counter

from helpers.api.datasets import MULTI_GROUP_REPORT

from khadbot.api.night import FIRST_DEATH_TEMPLATE, briefing
from khadbot.api.schemas import BestPull, FirstDeath, MostFailedMechanic
from khadbot.encounter.analyst.verify import check_finding
from khadbot.encounter.models import Finding


def sections_by_pulls(ds, code: str) -> list[list]:
    """Records per group, groups in the order first pulled, pulls in time order (computed here, not by the API)."""
    pulls = sorted(ds.store.pulls(code), key=lambda p: (p.start_ms, p.fight_id))
    groups: dict = {}
    for p in pulls:
        r = ds.records(p.fight_id, code)
        groups.setdefault((p.encounter_id, p.difficulty), []).append(r)
    return list(groups.values())


def night(ds, code=None) -> dict:
    response = ds.client.get(f"/v1/reports/{code or ds.code}/night")
    assert response.status_code == 200, response.text
    return response.json()


# ===========================================================================
# Totals and sections
# ===========================================================================


def test_totals_and_section_counts(dataset):
    body = night(dataset)
    pulls = dataset.store.pulls(dataset.code)
    kills = sum(p.kill for p in pulls)
    assert body["totals"] == {
        "pulls": len(pulls),
        "kills": kills,
        "wipes": len(pulls) - kills,
        "duration_ms": sum(p.duration_ms for p in pulls),
    }
    assert sum(s["pulls"] for s in body["sections"]) == body["totals"]["pulls"]
    assert sum(s["kills"] for s in body["sections"]) == body["totals"]["kills"]
    assert sum(s["wipes"] for s in body["sections"]) == body["totals"]["wipes"]
    groups = sections_by_pulls(dataset, dataset.code)
    assert [(s["encounter_id"], s["difficulty"]) for s in body["sections"]] == [
        (g[0].pull.encounter_id, g[0].pull.difficulty) for g in groups
    ]


def test_multi_group_report_gives_two_sections(real_dataset):
    body = night(real_dataset, MULTI_GROUP_REPORT)
    assert len(body["sections"]) == 2
    assert {s["difficulty_name"] for s in body["sections"]} == {"heroic", "mythic"}
    covered = {s["difficulty_name"]: s["coverage"] for s in body["sections"]}
    assert covered["mythic"] == "none"  # no encounter file covers Mythic Nymrissa


def test_unknown_report(client):
    assert client.get("/v1/reports/NOPE/night").json()["code"] == "report_not_found"


def test_section_values_match_the_records(dataset):
    body = night(dataset)
    for section, group in zip(body["sections"], sections_by_pulls(dataset, dataset.code), strict=True):
        m = len(group)
        assert section["pulls"] == m
        assert section["kills"] == sum(r.pull.kill for r in group)
        enc = group[0].enc
        coverages = {r.digest.coverage for r in group}
        assert section["coverage"] == ("none" if enc is None else "partial" if "partial" in coverages else "full")

        # --- most common first death ---
        firsts = [
            (r.deaths[0].killing_ability_id, r.deaths[0].killing_ability_name) if r.deaths else None for r in group
        ]
        counts = Counter(f for f in firsts if f is not None)
        fd = section["most_common_first_death"]
        if not counts:
            assert fd is None
        else:
            top_n = max(counts.values())
            assert (fd["ability_id"], fd["ability_name"]) in {k for k, n in counts.items() if n == top_n}
            top = (fd["ability_id"], fd["ability_name"])
            assert fd["pulls"] == top_n and fd["of_pulls"] == m
            assert fd["refs"] == [r.deaths[0].ref for r, f in zip(group, firsts, strict=True) if f == top]
            assert fd["pull_refs"] == [f"ev:{r.pull.pull_key}:pull" for r in group]

        # --- trend ---
        assert [t["fight"] for t in section["trend"]] == [r.pull.fight_id for r in group]
        for i, (t, r) in enumerate(zip(section["trend"], group, strict=True), start=1):
            assert t["ordinal"] == i and t["label"] == f"P{i}"
            assert t["outcome"] == ("kill" if r.pull.kill else "wipe")
            assert t["boss_hp_pct"] == (0 if r.pull.kill else r.pull.boss_hp_pct)
            assert t["boss_pct"] == r.pull.boss_pct
            assert t["duration_ms"] == r.pull.duration_ms
            assert t["deaths_before_cutoff"] == sum(not d.after_cutoff for d in r.deaths)
            assert t["coverage"] == r.digest.coverage
            first = firsts[i - 1]
            assert t["first_death_is_top"] == (fd is not None and first == (fd["ability_id"], fd["ability_name"]))

        # --- mechanic table ---
        expected: dict[str, dict] = {}
        for r in group:
            for stat in r.mechanics:
                if not (stat.hits or stat.failures):
                    continue
                row = expected.setdefault(
                    stat.mechanic_id, {"failures": 0, "hits": 0, "damage_taken": 0, "pulls_affected": 0, "refs": []}
                )
                row["failures"] += stat.failures
                row["hits"] += stat.hits
                row["damage_taken"] += stat.damage_taken
                row["pulls_affected"] += 1
                row["refs"].append(stat.ref)
        got = {x["mechanic_id"]: {k: x[k] for k in expected.get(x["mechanic_id"], {})} for x in section["mechanics"]}
        assert got == expected
        order = [(-x["failures"], -x["damage_taken"]) for x in section["mechanics"]]
        assert order == sorted(order)

        # --- most failed mechanic ---
        mf = section["most_failed_mechanic"]
        failing = {k: v for k, v in expected.items() if v["failures"]}
        if not failing:
            assert mf is None
        else:
            assert mf["failures"] == max(v["failures"] for v in failing.values())
            assert mf["failures"] == expected[mf["mechanic_id"]]["failures"]
            pulls_failed = [
                s.ref for r in group for s in r.mechanics if s.mechanic_id == mf["mechanic_id"] and s.failures
            ]
            assert mf["pulls_failed"] == len(pulls_failed) and mf["refs"] == pulls_failed

        # --- best pull ---
        best = section["best_pull"]
        kills = [r for r in group if r.pull.kill]
        if kills:
            pick = kills[0]
        else:
            inf = float("inf")
            pick = min(
                group,
                key=lambda r: (
                    inf if r.pull.boss_hp_pct is None else r.pull.boss_hp_pct,
                    inf if r.pull.boss_pct is None else r.pull.boss_pct,
                ),
            )
        assert best["fight"] == pick.pull.fight_id
        assert best["ordinal"] == group.index(pick) + 1
        assert best["ref"] == f"ev:{pick.pull.pull_key}:pull"
        assert best["phase_reached"] == pick.digest.phase_reached

        assert section["findings_available"] == 0 and section["findings_stale"] == 0


# ===========================================================================
# Briefing
# ===========================================================================


def _verify(ds, line: dict) -> str | None:
    data = ds.encounter_data()
    finding = Finding(id="brief-1", claim=line["text"], refs=line["refs"], confidence="high", scope="cross_pull")
    return check_finding(finding, data)


def test_briefing_lines_pass_the_verify_number_check(dataset):
    lines = [line for s in night(dataset)["sections"] for line in s["briefing"]]
    assert lines
    for line in lines:
        assert _verify(dataset, line) is None, line


def test_briefing_order_and_refs(dataset):
    for section in night(dataset)["sections"]:
        kinds = [line["kind"] for line in section["briefing"]]
        assert kinds == [k for k in ("first_death", "most_failed_mechanic", "best_pull") if k in kinds]
        by_kind = {line["kind"]: line for line in section["briefing"]}
        if section["most_common_first_death"] is None:
            assert "first_death" not in by_kind
        else:
            fd = section["most_common_first_death"]
            assert by_kind["first_death"]["refs"] == fd["refs"] + fd["pull_refs"]
        assert ("most_failed_mechanic" in by_kind) == (section["most_failed_mechanic"] is not None)
        assert by_kind["best_pull"]["refs"] == [section["best_pull"]["ref"]]
        for line in section["briefing"]:
            for ref in line["refs"]:
                assert dataset.client.get(f"/v1/evidence/{ref}").status_code == 200


def test_briefing_skips_missing_values_and_handles_singulars():
    assert briefing(None, None, {}, None, []) == []
    fd = FirstDeath(
        ability_id=None, ability_name=None, pulls=1, of_pulls=1, refs=["ev:R:1:death:1"], pull_refs=["ev:R:1:pull"]
    )
    mf = MostFailedMechanic(mechanic_id="m", name="Swirl", failures=1, pulls_failed=1, refs=["ev:R:1:mech:m"])
    best = BestPull(
        fight=1, ordinal=1, label="P1", outcome="wipe", boss_hp_pct=None, boss_pct=None, phase_reached=None,
        phase_name=None, ref="ev:R:1:pull",
    )  # fmt: skip
    lines = briefing(fd, mf, {"m": [(1, 1, "ev:R:1:mech:m")]}, best, ["ev:R:1:pull"])
    assert [line.text for line in lines] == [
        FIRST_DEATH_TEMPLATE.format(ability="An unknown ability", n=1, m=1, pulls_word="pull"),
        "Swirl was failed on 1 of 1 pull, most on P1 (1 failure).",
        "Best pull: P1, a wipe.",
    ]


def test_best_wipe_line_states_boss_health_and_phase():
    best = BestPull(
        fight=4, ordinal=2, label="P2", outcome="wipe", boss_hp_pct=36.84, boss_pct=30.0, phase_reached="p2",
        phase_name="Stage Two", ref="ev:R:4:pull",
    )  # fmt: skip
    (line,) = briefing(None, None, {}, best, ["ev:R:3:pull", "ev:R:4:pull"])
    assert line.text == "Best pull: P2, boss at 36.8% health, reached Stage Two."
