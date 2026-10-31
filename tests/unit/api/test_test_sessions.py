"""
tests/unit/api/test_test_sessions.py

Test sessions against the hosted API: a test user's session reads the reports marked for it, other test users and
visitors get 404 for them, and two test users never see each other's jobs. The users hold no WCL token, so their
ingests run on the app's auth.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient
from helpers.api.hosted import BASE, CSRF, Harness, build_harness

from khadbot.auth.dev_sessions import issue_session
from khadbot.auth.routes import SESSION_COOKIE
from khadbot.config import DevSessionsConfig
from khadbot.jobs.status import JobState

CODE = "SYNTH01"
ON = DevSessionsConfig(enabled=True)


@pytest.fixture
def h(tmp_path, service, wcl, auth_store, clock) -> Harness:
    return build_harness(tmp_path, service, wcl, auth_store, clock)


def browser(h: Harness, name: str | None, reports: list[str] | None = None) -> TestClient:
    client = TestClient(h.app, base_url=BASE)
    if name is not None:
        issued = asyncio.run(issue_session(h.store, ON, name, reports=reports or []))
        client.cookies.set(SESSION_COOKIE, issued.cookie)
    return client


def test_marked_report_is_readable_by_its_user_only(h):
    h.ingest_offline()
    alice, bob, visitor = browser(h, "alice", [CODE]), browser(h, "bob"), browser(h, None)
    assert alice.get(f"/v1/reports/{CODE}/night").status_code == 200
    for other in (bob, visitor):
        resp = other.get(f"/v1/reports/{CODE}/night")
        assert (resp.status_code, resp.json()["code"]) == (404, "report_not_found")
    assert h.wcl.report_checks == []  # nothing asked Warcraft Logs


def test_two_users_can_both_read_a_report_marked_for_both(h):
    h.ingest_offline()
    alice, bob = browser(h, "alice", [CODE]), browser(h, "bob", [CODE])
    assert alice.get(f"/v1/reports/{CODE}").status_code == 200
    assert bob.get(f"/v1/reports/{CODE}").status_code == 200


def test_users_do_not_see_each_others_jobs(h):
    alice, bob = browser(h, "alice", [CODE]), browser(h, "bob", [CODE])
    started = alice.post("/v1/reports", json={"source": CODE}, headers=CSRF)
    assert started.status_code == 202, started.text
    job_id = started.json()["id"]
    row = h.backend.rows[job_id]
    assert row.auth_kind == "app"  # a test user has no WCL token, so the job runs on the app's auth
    for call in (
        lambda c: c.get(f"/v1/jobs/{job_id}"),
        lambda c: c.get(f"/v1/jobs/{job_id}/events"),
        lambda c: c.post(f"/v1/jobs/{job_id}/cancel", headers=CSRF),
    ):
        resp = call(bob)
        assert (resp.status_code, resp.json()["code"]) == (404, "job_not_found")
    assert alice.get(f"/v1/jobs/{job_id}").status_code == 200
    assert h.backend.rows[job_id].state is JobState.QUEUED
