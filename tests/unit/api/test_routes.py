"""
tests/unit/api/test_routes.py

Every read endpoint and its error codes, on the synthetic report and on the recorded fixture reports. Expected
values come from the encounter pipeline itself (``build_pull_records``, ``evidence.resolve``), never hard-coded
numbers, so the tests survive changes to ingest and analysis.
"""

from __future__ import annotations

from urllib.parse import quote

import orjson
import pytest
from helpers.api.datasets import MULTI_GROUP_REPORT

from khadbot.api.views import EVENT_ROW_CAP, MAX_EVENT_WINDOW_MS
from khadbot.encounter.config_files import WCL_DIFFICULTY
from khadbot.encounter.evidence import pull_summary


def records(ds, fight: int):
    return ds.records(fight)


def evidence(client, ref: str):
    return client.get(f"/v1/evidence/{quote(ref, safe='')}")


def assert_error(response, status: int, code: str) -> None:
    assert response.status_code == status, response.text
    body = response.json()
    assert set(body) == {"code", "message"}
    assert body["code"] == code
    assert body["message"]


# ===========================================================================
# Health and reports
# ===========================================================================


def test_health(dataset, client):
    body = client.get("/v1/health").json()
    assert body["data_dir"] == str(dataset.data_dir)
    assert (body["status"], body["api_version"], body["mode"]) == ("ok", "1.0.0", "local")
    assert not any(body["features"].values())


def test_reports_lists_every_stored_report_with_groups(dataset, client):
    body = client.get("/v1/reports").json()
    assert [r["code"] for r in body["reports"]] == dataset.store.reports()
    assert body["total"] == len(dataset.store.reports())
    assert body["truncated"] is False
    for report in body["reports"]:
        pulls = sorted(dataset.store.pulls(report["code"]), key=lambda p: p.start_ms)
        assert report["pulls"] == len(pulls)
        assert sum(g["pulls"] for g in report["groups"]) == len(pulls)
        first_seen = list(dict.fromkeys((p.encounter_id, p.difficulty) for p in pulls))
        assert [(g["encounter_id"], g["difficulty"]) for g in report["groups"]] == first_seen
        for g in report["groups"]:
            assert g["difficulty_name"] == WCL_DIFFICULTY.get(g["difficulty"])
        meta = orjson.loads((dataset.data_dir / "raw" / report["code"] / "report.json").read_bytes())
        assert report["title"] == meta["title"]


def test_reports_empty_data_directory(tmp_path):
    from fastapi.testclient import TestClient

    from khadbot.api import create_app

    body = TestClient(create_app(mode="local", data_dir=tmp_path)).get("/v1/reports").json()
    assert body == {"reports": [], "truncated": False, "total": 0}
    assert not (tmp_path / "khadbot.sqlite").exists()  # reading never creates the findings database


def test_report_groups_and_pull_summaries(dataset, client):
    body = client.get(f"/v1/reports/{dataset.code}").json()
    pulls = sorted(dataset.store.pulls(dataset.code), key=lambda p: p.start_ms)
    assert body["code"] == dataset.code
    assert body["total"] == len(body["groups"])
    seen = []
    for group in body["groups"]:
        assert group["total"] == len(group["pulls"])
        assert group["truncated"] is False
        assert [p["ordinal"] for p in group["pulls"]] == list(range(1, len(group["pulls"]) + 1))
        for row in group["pulls"]:
            expected = pull_summary(records(dataset, row["fight"]))
            assert row["label"] == f"P{row['ordinal']}"
            for field in (
                "outcome",
                "duration_ms",
                "boss_pct",
                "boss_hp_pct",
                "phase_reached",
                "deaths",
                "deaths_before_cutoff",
                "coverage",
                "anomalies",
                "ref",
            ):
                assert row[field] == expected[field], field
            seen.append(row["fight"])
    assert sorted(seen) == sorted(p.fight_id for p in pulls)


def test_multi_group_report_has_two_groups(real_dataset):
    body = real_dataset.client.get(f"/v1/reports/{MULTI_GROUP_REPORT}").json()
    assert len(body["groups"]) == 2
    assert {g["difficulty_name"] for g in body["groups"]} == {"heroic", "mythic"}


def test_unknown_report(client):
    for path in ("", "/night", "/pulls/1/digest", "/pulls/1/timeline", "/pulls/1/findings"):
        assert_error(client.get(f"/v1/reports/NOPE{path}"), 404, "report_not_found")
    assert_error(client.get("/v1/reports/NOPE/pulls/1/events?t_from_ms=0&t_to_ms=1000"), 404, "report_not_found")


@pytest.mark.parametrize("code", ["bad:code", "bad*code", "bad|code"])
def test_code_no_storage_key_can_hold_is_an_unknown_report(client, code):
    for path in ("", "/night", "/pulls/1/digest"):
        assert_error(client.get(f"/v1/reports/{code}{path}"), 404, "report_not_found")


def test_unknown_pull(dataset, client):
    for path in ("digest", "timeline", "findings", "events?t_from_ms=0&t_to_ms=1000"):
        assert_error(client.get(f"/v1/reports/{dataset.code}/pulls/9999/{path}"), 404, "pull_not_found")


def test_bad_parameter_uses_the_error_body(dataset, client):
    assert_error(client.get(f"/v1/reports/{dataset.code}/pulls/abc/digest"), 422, "bad_request")
    assert_error(client.get(f"/v1/reports/{dataset.code}/pulls/{dataset.fights[0]}/events"), 422, "bad_request")


# ===========================================================================
# Digest and timeline
# ===========================================================================


def test_digest_equals_build_digest(dataset, client):
    for fight in dataset.fights:
        expected = dataset.digest(fight)
        got = client.get(f"/v1/reports/{dataset.code}/pulls/{fight}/digest").json()
        assert got == expected.model_dump(mode="json")


def test_full_digest_has_every_record(dataset, client):
    for fight in dataset.fights:
        r = records(dataset, fight)
        got = client.get(f"/v1/reports/{dataset.code}/pulls/{fight}/digest?full=true").json()
        assert sorted(d["ref"] for d in got["deaths"]) == sorted(d.ref for d in r.deaths)
        assert [d["after_cutoff"] for d in got["deaths"]] == sorted(d.after_cutoff for d in r.deaths)
        assert sorted(m["ref"] for m in got["mechanics"]) == sorted(m.ref for m in r.mechanics)
        for category in ("defensive", "offensive", "utility"):
            uses = [c.ref for c in r.cooldowns if c.category == category]
            assert [c["ref"] for c in got[f"{category}_cooldowns"]] == uses
        assert [w["ref"] for w in got["coverage_windows"]] == [w.ref for w in r.windows]
        capped = r.digest.model_dump(mode="json")
        for field in (
            "pull_key",
            "outcome",
            "coverage",
            "anomalies",
            "produced_from",
            "unregistered",
            "aggro",
            "friendly_damage",
        ):
            assert got[field] == capped[field]


def test_timeline_markers_come_from_the_records(dataset, client):
    from khadbot.encounter.analysis.mechanics import mechanic_clusters

    for fight in dataset.fights:
        r = records(dataset, fight)
        got = client.get(f"/v1/reports/{dataset.code}/pulls/{fight}/timeline").json()
        assert got["duration_ms"] == r.pull.duration_ms
        assert got["cutoff_ms"] == r.annotated.cutoff_ms
        assert [(p["id"], p["start_ms"], p["end_ms"]) for p in got["phases"]] == [
            (s.phase_id, s.start_ms, s.end_ms) for s in r.annotated.phases
        ]
        names = {p.id: p.name for p in r.enc.phases} if r.enc else {}
        assert all(p["name"] == names[p["id"]] for p in got["phases"])
        assert [(d["t_ms"], d["ref"], d["after_cutoff"]) for d in got["deaths"]] == [
            (d.t_ms, d.ref, d.after_cutoff) for d in r.deaths
        ]
        clusters = sum(len(mechanic_clusters(r.annotated, m.mechanic_id, r.actors)) for m in r.mechanics)
        assert len(got["mechanics"]) == clusters
        assert [m["t_ms"] for m in got["mechanics"]] == sorted(m["t_ms"] for m in got["mechanics"])
        assert sum(m["failures"] for m in got["mechanics"]) <= sum(m.failures for m in r.mechanics)
        assert [(c["ref"], c["category"]) for c in got["cooldowns"]] == [(c.ref, c.category) for c in r.cooldowns]
        assert sorted(w["ref"] for w in got["windows"]) == sorted(w.ref for w in r.windows)


def test_timeline_refs_resolve(dataset, client):
    fight = dataset.fights[0]
    got = client.get(f"/v1/reports/{dataset.code}/pulls/{fight}/timeline").json()
    refs = {got["ref"]}
    for layer in ("deaths", "mechanics", "cooldowns", "windows"):
        refs |= {m["ref"] for m in got[layer][:3]}
    for ref in refs:
        assert evidence(client, ref).status_code == 200, ref


# ===========================================================================
# Events
# ===========================================================================


def _busiest_window(r) -> tuple[int, int]:
    """A 30 s window holding more rows than the cap when the pull has that many, else the whole first 30 s."""
    import polars as pl

    frame = r.annotated.frame
    starts = frame["t_ms"].to_list()[:: max(1, frame.height // 50)]
    best = max(starts, key=lambda t: frame.filter(pl.col("t_ms").is_between(t, t + MAX_EVENT_WINDOW_MS)).height)
    return best, best + MAX_EVENT_WINDOW_MS


def test_events_rows_resolve_like_event_refs(dataset, client):
    fight = dataset.fights[0]
    r = records(dataset, fight)
    t_from, t_to = _busiest_window(r)
    got = client.get(f"/v1/reports/{dataset.code}/pulls/{fight}/events?t_from_ms={t_from}&t_to_ms={t_to}").json()
    in_window = r.annotated.frame.filter(r.annotated.frame["t_ms"].is_between(t_from, t_to))
    assert got["total"] == in_window.height
    assert got["truncated"] == (in_window.height > EVENT_ROW_CAP)
    assert len(got["rows"]) == min(EVENT_ROW_CAP, in_window.height)
    assert got["refs"] == [row["ref"] for row in got["rows"]]
    assert [row["seq"] for row in got["rows"]] == sorted(row["seq"] for row in got["rows"])
    for row in got["rows"][:: max(1, len(got["rows"]) // 5)]:
        assert evidence(client, row["ref"]).json()["record"] == row


def test_events_cap_rows(real_dataset):
    ds = real_dataset
    r = records(ds, 15)
    t_from, t_to = _busiest_window(r)
    got = ds.client.get(f"/v1/reports/{ds.code}/pulls/15/events?t_from_ms={t_from}&t_to_ms={t_to}").json()
    assert got["total"] > EVENT_ROW_CAP
    assert got["truncated"] is True
    assert len(got["rows"]) == EVENT_ROW_CAP
    assert got["note"]


def test_events_filters(dataset, client):
    fight = dataset.fights[0]
    r = records(dataset, fight)
    death = r.deaths[0]
    base = f"/v1/reports/{dataset.code}/pulls/{fight}/events?t_from_ms={max(0, death.t_ms - 5000)}&t_to_ms={death.t_ms}"
    by_type = client.get(base + "&types=death&types=damage").json()
    assert by_type["rows"] and {row["type"] for row in by_type["rows"]} <= {"death", "damage"}
    by_actor = client.get(base + f"&actor={quote(death.name)}").json()
    assert by_actor["rows"] and all(death.name in (row.get("source"), row.get("target")) for row in by_actor["rows"])
    by_id = client.get(base + f"&actor={death.actor_id}").json()
    assert by_id["total"] == by_actor["total"]
    if death.top_hits and death.top_hits[0].ability_id is not None:
        ability = death.top_hits[0].ability_id
        by_ability = client.get(base + f"&ability_id={ability}").json()
        assert by_ability["rows"] and {row["ability_id"] for row in by_ability["rows"]} == {ability}
    none = client.get(base + "&types=no_such_type").json()
    assert none["rows"] == [] and none["total"] == 0 and "Types present" in none["note"]


def test_events_rejects_bad_windows(dataset, client):
    base = f"/v1/reports/{dataset.code}/pulls/{dataset.fights[0]}/events"
    assert_error(client.get(f"{base}?t_from_ms=0&t_to_ms={MAX_EVENT_WINDOW_MS + 1}"), 400, "bad_window")
    assert_error(client.get(f"{base}?t_from_ms=5000&t_to_ms=4000"), 400, "bad_window")
    assert client.get(f"{base}?t_from_ms=0&t_to_ms={MAX_EVENT_WINDOW_MS}").status_code == 200


def test_events_unknown_actor(dataset, client):
    base = f"/v1/reports/{dataset.code}/pulls/{dataset.fights[0]}/events?t_from_ms=0&t_to_ms=1000"
    assert_error(client.get(base + "&actor=Nobody%20Here"), 400, "unknown_actor")


# ===========================================================================
# Evidence
# ===========================================================================


def _one_ref_per_kind(r) -> dict[str, str]:
    refs = {"pull": f"ev:{r.pull.pull_key}:pull", "death": r.deaths[0].ref, "mech": r.mechanics[0].ref}
    refs["cd"] = r.cooldowns[0].ref
    refs["win"] = r.windows[0].ref
    seq = r.annotated.frame["seq"][0]
    refs["event"] = f"ev:{r.pull.pull_key}:event:{seq}"
    return refs


def test_every_ref_kind_resolves(dataset, client):

    data = dataset.encounter_data()
    r = next(records(dataset, f) for f in dataset.fights if (x := records(dataset, f)).cooldowns and x.windows)
    for kind, ref in _one_ref_per_kind(r).items():
        response = evidence(client, ref)
        assert response.status_code == 200, (kind, response.text)
        body = response.json()
        assert body == {"ref": ref, "kind": kind, "record": data.resolve(ref)}


def test_plan_refs_are_not_implemented(client):
    assert_error(evidence(client, "plan:abc:3"), 501, "plan_refs_unavailable")


def test_unresolvable_refs(dataset, client):
    fight = dataset.fights[0]
    for ref in (
        "nonsense",
        f"ev:{dataset.code}:{fight}:bogus:1",
        f"ev:{dataset.code}:{fight}:death:9999",
        f"ev:{dataset.code}:{fight}:mech:no_such_mechanic",
        "ev:NOPE:1:pull",
        "ev:bad*code:1:pull",
    ):
        assert_error(evidence(client, ref), 404, "unresolvable_ref")


def test_cors_allows_only_the_local_dev_origins(client):
    for origin in ("http://localhost:3000", "http://127.0.0.1:3000"):
        r = client.get("/v1/health", headers={"Origin": origin})
        assert r.headers["access-control-allow-origin"] == origin
    r = client.get("/v1/health", headers={"Origin": "https://example.com"})
    assert "access-control-allow-origin" not in r.headers


@pytest.mark.parametrize("path", ["/v1/health", "/v1/reports"])
def test_only_get_is_served(client, path):
    assert client.post(path).status_code == 405
