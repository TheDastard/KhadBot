"""
tests/unit/api/test_review_routes.py

The pull review routes on the hosted API harness with a fake analyst: sign-in, CSRF and report access, the status a
review button shows, a review job followed to its findings, findings shared with a second user for free, the
identical review in flight returned to a second asker, and the quota boundary with its typed 429.
"""

from __future__ import annotations

import pytest
from helpers.api.hosted import Harness, build_harness
from helpers.encounter.review_fakes import respond
from helpers.encounter.usage_fake import UsageFakeModel

from khadbot.config import reset_config

CODE = "SYNTH01"
URL = f"/v1/reports/{CODE}/pulls/1/review"
FINDINGS = f"/v1/reports/{CODE}/pulls/1/findings"
MODEL = "test-analyst"


@pytest.fixture
def h(tmp_path, service, wcl, auth_store, clock, role_fakes, monkeypatch) -> Harness:
    monkeypatch.setenv("ANALYST_MODEL", MODEL)
    reset_config()
    role_fakes["analyst"] = UsageFakeModel(respond=respond, model_name=MODEL)
    harness = build_harness(tmp_path, service, wcl, auth_store, clock)
    harness.env.context.findings = harness.app.state.ctx.findings
    harness.ingest_offline()
    harness.process_offline()
    yield harness
    reset_config()


def readable_by(h, *names):
    for name in names:
        h.wcl.readable[h.token_of(name)] = {CODE}


def reviews(h):
    return [r for r in h.backend.rows.values() if r.kind.value == "review"]


def test_a_visitor_cannot_review_but_can_see_what_a_review_would_do(h):
    h.wcl.readable["app"] = {CODE}  # public
    visitor = h.actor()
    assert visitor.post(URL, json={}).json()["code"] == "not_signed_in"
    seen = visitor.get(URL).json()
    assert (seen["cached"], seen["quota"], seen["job"], seen["model"]) == (False, None, None, MODEL)
    assert seen["estimate"]["total_tokens"] == seen["estimate"]["digest_tokens"] + seen["estimate"]["output_tokens"] > 0


def test_a_report_the_user_cannot_read_is_404_and_an_unloaded_pull_is_404(h):
    jaina = h.actor("Jaina")
    for resp in (jaina.get(URL), jaina.post(URL, json={})):
        assert (resp.status_code, resp.json()["code"]) == (404, "report_not_found")
    thrall = h.actor("Thrall")  # Jaina's denial is cached for a while: a reader of the report asks for a missing pull
    readable_by(h, "Thrall")
    resp = thrall.post(f"/v1/reports/{CODE}/pulls/99/review", json={})
    assert (resp.status_code, resp.json()["code"]) == (404, "pull_not_found")
    assert reviews(h) == []


def test_starting_a_review_needs_the_csrf_header_and_a_valid_depth(h):
    jaina = h.actor("Jaina")
    readable_by(h, "Jaina")
    assert jaina.post(URL, csrf=False, json={}).json()["code"] == "csrf_failed"
    assert jaina.post(URL, json={"depth": "bottomless"}).status_code == 422
    assert reviews(h) == []


def test_the_status_shows_the_quota_before_and_after_a_review_is_queued(h, monkeypatch):
    monkeypatch.setenv("KHADBOT_REVIEWS_PER_DAY", "3")
    jaina = h.actor("Jaina")
    readable_by(h, "Jaina")
    quota = jaina.get(URL).json()["quota"]
    assert (quota["limit"], quota["used"], quota["remaining"], quota["window_hours"]) == (3, 0, 3, 24)
    started = jaina.post(URL, json={})
    assert started.status_code == 202
    body = started.json()
    assert (body["cached"], body["quota"]["used"], body["job"]["kind"], body["job"]["pulls"]) == (
        False,
        1,
        "review",
        [],
    )
    assert body["job"]["can_cancel"] is False
    status = jaina.get(URL).json()
    assert status["quota"]["remaining"] == 2 and status["job"]["id"] == body["job"]["id"]


def test_a_review_runs_to_findings_and_the_next_user_gets_them_free(h):
    jaina, thrall = h.actor("Jaina"), h.actor("Thrall")
    readable_by(h, "Jaina", "Thrall")
    job_id = jaina.post(URL, json={}).json()["job"]["id"]
    h.run_worker().join(timeout=30)

    job = jaina.get(f"/v1/jobs/{job_id}").json()
    assert (job["kind"], job["state"], job["pct"], job["error"]) == ("review", "done", 100.0, None)
    found = jaina.get(FINDINGS).json()
    assert [f["claim"] for entry in found["findings"] for f in entry["findings"]] == ["The pull was reviewed"]
    assert jaina.get(URL).json()["cached"] is True

    again = thrall.post(URL, json={})  # the same data, prompts, model and depth: a cache hit shared with everyone
    assert again.status_code == 200
    assert (again.json()["cached"], again.json()["job"], again.json()["quota"]["used"]) == (True, None, 0)
    assert len(reviews(h)) == 1  # no second job
    other_depth = thrall.get(URL, params={"depth": "deep"}).json()
    assert other_depth["cached"] is False  # depth is part of the key


def test_a_second_user_asking_while_it_runs_follows_the_same_job_and_cannot_cancel_it(h):
    jaina, thrall = h.actor("Jaina"), h.actor("Thrall")
    readable_by(h, "Jaina", "Thrall")
    first = jaina.post(URL, json={}).json()["job"]
    second = thrall.post(URL, json={})
    assert second.status_code == 200
    assert second.json()["job"]["id"] == first["id"] and second.json()["job"]["can_cancel"] is False
    assert second.json()["quota"]["used"] == 0  # following costs nothing
    assert thrall.get(f"/v1/jobs/{first['id']}").status_code == 200
    gone = jaina.post(f"/v1/jobs/{first['id']}/cancel")
    assert (gone.status_code, gone.json()["code"]) == (404, "job_not_found")  # reviews run to the end


def test_the_quota_boundary_answers_quota_exceeded_with_when_a_slot_frees(h, monkeypatch):
    monkeypatch.setenv("KHADBOT_REVIEWS_PER_DAY", "1")
    jaina = h.actor("Jaina")
    readable_by(h, "Jaina")
    assert jaina.post(URL, json={}).status_code == 202
    over = jaina.post(f"/v1/reports/{CODE}/pulls/2/review", json={})
    assert over.status_code == 429
    body = over.json()
    assert body["code"] == "quota_exceeded"
    assert (body["quota"]["limit"], body["quota"]["used"], body["quota"]["remaining"]) == (1, 1, 0)
    assert body["quota"]["next_slot_at"] is not None and 0 < body["wait_s"] <= 24 * 3600
    assert int(over.headers["retry-after"]) >= 1
    assert len(reviews(h)) == 1


def test_a_cached_review_works_even_when_the_quota_is_spent(h, monkeypatch):
    jaina = h.actor("Jaina")
    readable_by(h, "Jaina")
    jaina.post(URL, json={})
    h.run_worker().join(timeout=30)
    monkeypatch.setenv("KHADBOT_REVIEWS_PER_DAY", "1")  # the one allowed review is used
    again = jaina.post(URL, json={})
    assert (again.status_code, again.json()["cached"]) == (200, True)
