"""
tests/unit/api/test_watch_routes.py

The live report watch routes through FastAPI's TestClient on the hosted API harness: signed-out callers get 401, a
report the caller cannot read answers 404, the CSRF header is required to start and stop, a user without a Warcraft
Logs sign-in cannot start one, a second request returns the first and only the owner sees or stops a watch.
"""

from __future__ import annotations

import pytest
from helpers.api.hosted import CSRF, Harness, build_harness

from khadbot.jobs.watch_store import StopReason

CODE = "SYNTH01"
URL = f"/v1/reports/{CODE}/watch"


@pytest.fixture
def h(tmp_path, service, wcl, auth_store, clock) -> Harness:
    return build_harness(tmp_path, service, wcl, auth_store, clock)


def delete(actor, url=URL, *, csrf=True):
    return actor.client.delete(url, headers=CSRF if csrf else {})


def readable_by(h, *names):
    for name in names:
        h.wcl.readable[h.token_of(name)] = {CODE}


def test_signed_out_visitors_get_401(h):
    visitor = h.actor()
    assert visitor.post(URL).json()["code"] == "not_signed_in"
    assert visitor.get(URL).status_code == 401
    assert delete(visitor).status_code == 401


def test_a_report_the_user_cannot_read_is_404_on_every_route(h):
    jaina = h.actor("Jaina")
    for resp in (jaina.post(URL), jaina.get(URL), delete(jaina)):
        assert (resp.status_code, resp.json()["code"]) == (404, "report_not_found")
    assert h.queue.watches.store._backend.rows == {}


def test_starting_and_stopping_need_the_csrf_header(h):
    jaina = h.actor("Jaina")
    readable_by(h, "Jaina")
    assert jaina.post(URL, csrf=False).json()["code"] == "csrf_failed"
    assert delete(jaina, csrf=False).json()["code"] == "csrf_failed"


def test_a_user_without_a_wcl_sign_in_cannot_start_a_watch(h):
    jaina = h.actor("Jaina", tokens=False)
    h.wcl.readable["app"] = {CODE}  # a public report: readable, but there is no token to poll with
    resp = jaina.post(URL)
    assert (resp.status_code, resp.json()["code"]) == (409, "wcl_sign_in_required")
    assert h.queue.watches.store._backend.rows == {}


def test_starting_returns_202_then_the_same_watch_with_200(h):
    jaina = h.actor("Jaina")
    readable_by(h, "Jaina")
    first = jaina.post(URL)
    assert first.status_code == 202, first.text
    body = first.json()
    assert (body["report_code"], body["state"], body["stop_reason"]) == (CODE, "active", None)
    assert (body["polls"], body["fights_found"], body["poll_interval_s"]) == (0, 0, 180.0)
    assert body["stops_by"] is not None and body["next_poll_at"] is not None
    again = jaina.post(URL)
    assert (again.status_code, again.json()["started_at"]) == (200, body["started_at"])
    assert len(h.queue.watches.store._backend.rows) == 1
    row = next(iter(h.queue.watches.store._backend.rows.values()))
    assert row.user_id == jaina.user.id and "fake-access" not in row.model_dump_json()  # a user ID, never a token


def test_someone_elses_watch_is_a_409_to_start_and_a_404_to_see_or_stop(h):
    readable_by(h, "Jaina", "Thrall")
    jaina, thrall = h.actor("Jaina"), h.actor("Thrall")
    assert jaina.post(URL).status_code == 202
    resp = thrall.post(URL)
    assert (resp.status_code, resp.json()["code"]) == (409, "watch_in_progress")
    assert (thrall.get(URL).status_code, thrall.get(URL).json()["code"]) == (404, "watch_not_found")
    assert (delete(thrall).status_code, delete(thrall).json()["code"]) == (404, "watch_not_found")
    assert jaina.get(URL).json()["state"] == "active"  # untouched


def test_get_shows_the_watch_and_404s_before_any(h):
    jaina = h.actor("Jaina")
    readable_by(h, "Jaina")
    assert jaina.get(URL).json()["code"] == "watch_not_found"
    jaina.post(URL)
    assert jaina.get(URL).json()["state"] == "active"


def test_delete_stops_the_watch_and_drops_its_queued_poll(h):
    jaina = h.actor("Jaina")
    readable_by(h, "Jaina")
    jaina.post(URL)
    row = next(iter(h.queue.watches.store._backend.rows.values()))
    resp = delete(jaina)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert (body["state"], body["stop_reason"], body["next_poll_at"], body["stops_by"]) == (
        "stopped",
        StopReason.USER.value,
        None,
        None,
    )
    assert h.connector.jobs[row.queue_job_id]["status"] == "cancelled"
    assert delete(jaina).json()["state"] == "stopped"  # stopping again returns it unchanged
    assert jaina.get(URL).json()["stop_reason"] == "user"


def test_a_stopped_watch_can_be_started_again(h):
    jaina = h.actor("Jaina")
    readable_by(h, "Jaina")
    jaina.post(URL)
    delete(jaina)
    again = jaina.post(URL)
    assert (again.status_code, again.json()["state"]) == (202, "active")


def test_delete_without_a_watch_is_404(h):
    jaina = h.actor("Jaina")
    readable_by(h, "Jaina")
    assert delete(jaina).json()["code"] == "watch_not_found"
