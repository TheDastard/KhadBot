"""
tests/unit/api/test_hosted.py

The hosted API through FastAPI's TestClient: sign-in over the in-memory store, WCL faked with respx, the queue on
procrastinate's in-memory connector, SYNTH01's stores. Every report and job route is called signed in and signed
out; another user's private report and another user's job both answer 404; the CORS and CSRF policy; and an ingest
followed over server-sent events until it is done.
"""

from __future__ import annotations

import asyncio
import json
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient
from helpers.api.hosted import Harness, build_harness

from khadbot.api import create_app
from khadbot.auth.routes import CSRF_HEADER
from khadbot.jobs import IngestReport, ProcessPull
from khadbot.jobs.status import JobState

CODE = "SYNTH01"
REPORT_ROUTES = [
    f"/v1/reports/{CODE}",
    f"/v1/reports/{CODE}/night",
    f"/v1/reports/{CODE}/pulls/1/digest",
    f"/v1/reports/{CODE}/pulls/1/timeline",
    f"/v1/reports/{CODE}/pulls/1/events?t_from_ms=0&t_to_ms=1000",
    f"/v1/reports/{CODE}/pulls/1/findings",
    "/v1/evidence/" + quote(f"ev:{CODE}:1:pull", safe=""),
]


@pytest.fixture
def h(tmp_path, service, wcl, auth_store, clock) -> Harness:
    return build_harness(tmp_path, service, wcl, auth_store, clock)


def sse_events(text: str) -> list[tuple[int, str, dict]]:
    events = []
    for block in text.strip().split("\n\n"):
        lines = [ln for ln in block.splitlines() if not ln.startswith(":")]
        if not lines:
            continue
        fields = dict(ln.split(": ", 1) for ln in lines)
        events.append((int(fields["id"]), fields["event"], json.loads(fields["data"])))
    return events


# ---------------------------------------------------------------------------
# Report access
# ---------------------------------------------------------------------------


class TestReportAccess:
    @pytest.mark.parametrize("url", REPORT_ROUTES)
    def test_signed_out_visitor_cannot_read_a_private_report(self, h, url):
        h.ingest_offline()
        resp = h.actor().get(url)
        assert resp.status_code == 404
        assert resp.json()["code"] == "report_not_found"

    @pytest.mark.parametrize("url", REPORT_ROUTES)
    def test_signed_in_user_without_access_gets_404(self, h, url):
        h.ingest_offline()
        resp = h.actor("Jaina").get(url)
        assert (resp.status_code, resp.json()["code"]) == (404, "report_not_found")

    @pytest.mark.parametrize("url", REPORT_ROUTES)
    def test_user_with_their_own_token_reads_it(self, h, url):
        h.ingest_offline()
        jaina = h.actor("Jaina")
        h.wcl.readable[h.token_of("Jaina")] = {CODE}
        assert jaina.get(url).status_code == 200
        assert h.actor("Thrall").get(url).status_code == 404  # another user, same report

    @pytest.mark.parametrize("url", REPORT_ROUTES)
    def test_public_report_is_readable_signed_in_or_out(self, h, url):
        h.ingest_offline()
        h.wcl.readable["app"] = {CODE}
        assert h.actor().get(url).status_code == 200
        assert h.actor("Jaina").get(url).status_code == 200

    def test_an_unresolvable_ref_is_the_same_404_for_everyone(self, h):
        resp = h.actor().get("/v1/evidence/nonsense")
        assert (resp.status_code, resp.json()["code"]) == (404, "unresolvable_ref")

    def test_plan_refs_stay_unavailable(self, h):
        assert h.actor().get("/v1/evidence/plan:abc:1").status_code == 501

    def test_access_is_checked_before_anything_is_read(self, h):
        """An unreadable report that was never ingested answers like one that was: 404 report_not_found."""
        resp = h.actor("Jaina").get(f"/v1/reports/{CODE}/night")
        assert (resp.status_code, resp.json()["code"]) == (404, "report_not_found")

    def test_wcl_down_fails_closed_with_503(self, h):
        import httpx

        from khadbot.wcl.client import GRAPHQL_CLIENT_URL

        h.ingest_offline()
        h.wcl.router.post(GRAPHQL_CLIENT_URL).mock(side_effect=httpx.ConnectError("down"))
        resp = h.actor().get(f"/v1/reports/{CODE}/night")
        assert (resp.status_code, resp.json()["code"]) == (503, "wcl_unavailable")

    def test_health_stays_open(self, h):
        assert h.actor().get("/v1/health").json()["status"] == "ok"

    def test_report_list_is_the_users_own(self, h):
        h.ingest_offline()
        jaina, thrall, visitor = h.actor("Jaina"), h.actor("Thrall"), h.actor()
        h.directory.codes[jaina.user.id] = [CODE]
        assert [r["code"] for r in jaina.get("/v1/reports").json()["reports"]] == [CODE]
        assert thrall.get("/v1/reports").json() == {"reports": [], "truncated": False, "total": 0}
        assert visitor.get("/v1/reports").json()["total"] == 0


# ---------------------------------------------------------------------------
# CORS and CSRF
# ---------------------------------------------------------------------------


class TestCorsAndCsrf:
    def test_preflight_from_the_web_origin_allows_credentials_post_delete_and_the_csrf_header(self, h):
        resp = h.actor().client.options(
            "/v1/reports",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": f"{CSRF_HEADER},content-type",
            },
        )
        assert resp.status_code == 200
        assert resp.headers["access-control-allow-origin"] == "http://localhost:3000"
        assert resp.headers["access-control-allow-credentials"] == "true"
        assert {"POST", "DELETE", "GET"} <= set(resp.headers["access-control-allow-methods"].split(", "))
        assert CSRF_HEADER.lower() in resp.headers["access-control-allow-headers"].lower()

    def test_a_foreign_origin_gets_no_cors_grant(self, h):
        resp = h.actor().client.get("/v1/health", headers={"Origin": "https://evil.example"})
        assert "access-control-allow-origin" not in resp.headers
        pre = h.actor().client.options(
            "/v1/reports", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"}
        )
        assert "access-control-allow-origin" not in pre.headers

    def test_a_response_to_the_web_origin_carries_the_credentials_grant(self, h):
        resp = h.actor().client.get("/v1/health", headers={"Origin": "http://localhost:3000"})
        assert resp.headers["access-control-allow-origin"] == "http://localhost:3000"
        assert resp.headers["access-control-allow-credentials"] == "true"

    def test_local_mode_cors_is_unchanged(self, tmp_path):
        client = TestClient(create_app(mode="local", data_dir=tmp_path))
        resp = client.options(
            "/v1/reports", headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "POST"}
        )
        assert resp.status_code == 400  # POST is not allowed
        ok = client.get("/v1/health", headers={"Origin": "http://localhost:3000"})
        assert "access-control-allow-credentials" not in ok.headers

    def test_state_changing_routes_need_the_csrf_header(self, h):
        jaina = h.actor("Jaina")
        resp = jaina.post("/v1/reports", csrf=False, json={"source": CODE})
        assert (resp.status_code, resp.json()["code"]) == (403, "csrf_failed")
        assert jaina.post("/v1/jobs/x/cancel", csrf=False).status_code == 403

    def test_state_changing_routes_refuse_a_foreign_origin(self, h):
        jaina = h.actor("Jaina")
        resp = jaina.post("/v1/reports", json={"source": CODE}, headers={"Origin": "https://evil.example"})
        assert (resp.status_code, resp.json()["code"]) == (403, "csrf_failed")

    def test_csrf_is_checked_before_sign_in(self, h):
        assert h.actor().post("/v1/reports", csrf=False, json={"source": CODE}).status_code == 403


# ---------------------------------------------------------------------------
# Ingest jobs
# ---------------------------------------------------------------------------


class TestIngestRoutes:
    def test_signed_out_job_routes_are_401(self, h):
        visitor = h.actor()
        assert visitor.post("/v1/reports", json={"source": CODE}).json()["code"] == "not_signed_in"
        assert visitor.get("/v1/jobs/x").status_code == 401
        assert visitor.get("/v1/jobs/x/events").status_code == 401
        assert visitor.post("/v1/jobs/x/cancel").status_code == 401

    def test_cannot_ingest_a_report_the_user_cannot_read(self, h):
        resp = h.actor("Jaina").post("/v1/reports", json={"source": CODE})
        assert (resp.status_code, resp.json()["code"]) == (404, "report_not_found")
        assert h.backend.rows == {}

    @pytest.mark.parametrize(
        "source",
        ["", "not a code!", "https://evil.example/reports/abc123", "https://www.warcraftlogs.com/guild/1", "../x"],
    )
    def test_bad_source(self, h, source):
        resp = h.actor("Jaina").post("/v1/reports", json={"source": source})
        assert resp.status_code in (400, 422)

    def test_start_returns_202_then_the_same_job_with_200(self, h):
        jaina = h.actor("Jaina")
        h.wcl.readable[h.token_of("Jaina")] = {CODE}
        first = jaina.post("/v1/reports", json={"source": f"https://www.warcraftlogs.com/reports/{CODE}#fight=1"})
        assert first.status_code == 202, first.text
        job = first.json()
        assert (job["report_code"], job["state"], job["error"]) == (CODE, "queued", None)
        row = h.backend.rows[job["id"]]
        assert (row.requested_by, row.auth_kind, row.spec["fights"]) == (jaina.user.id, "user", [1])
        assert "fake-access" not in json.dumps(row.spec)  # a user ID, never a token
        again = jaina.post("/v1/reports", json={"source": CODE, "fights": [1]})
        assert (again.status_code, again.json()["id"]) == (200, job["id"])

    def test_a_user_without_a_wcl_token_ingests_with_app_auth(self, h):
        jaina = h.actor("Jaina", tokens=False)
        h.wcl.readable["app"] = {CODE}
        job = jaina.post("/v1/reports", json={"source": CODE}).json()
        assert h.backend.rows[job["id"]].auth_kind == "app"

    def test_another_users_running_ingest_is_not_shown_to_you(self, h):
        h.wcl.readable["app"] = {CODE}
        first = h.actor("Jaina").post("/v1/reports", json={"source": CODE})
        assert first.status_code == 202
        second = h.actor("Thrall").post("/v1/reports", json={"source": CODE})
        assert (second.status_code, second.json()["code"]) == (409, "ingest_in_progress")
        assert first.json()["id"] not in second.text

    def test_other_users_and_missing_jobs_are_404(self, h):
        h.wcl.readable["app"] = {CODE}
        jaina, thrall = h.actor("Jaina"), h.actor("Thrall")
        job_id = jaina.post("/v1/reports", json={"source": CODE}).json()["id"]
        for call in (
            lambda a, i: a.get(f"/v1/jobs/{i}"),
            lambda a, i: a.get(f"/v1/jobs/{i}/events"),
            lambda a, i: a.post(f"/v1/jobs/{i}/cancel"),
        ):
            for actor, ident in ((thrall, job_id), (jaina, "missing")):
                resp = call(actor, ident)
                assert (resp.status_code, resp.json()["code"]) == (404, "job_not_found")
        assert h.backend.rows[job_id].state is JobState.QUEUED  # Thrall's cancel did nothing

    def test_process_jobs_are_not_exposed(self, h):
        h.wcl.readable["app"] = {CODE}
        jaina = h.actor("Jaina")
        job = jaina.post("/v1/reports", json={"source": CODE}).json()
        child = asyncio.run(h.queue.enqueue(ProcessPull(pull_key=f"{CODE}:1"), parent=h.backend.rows[job["id"]]))
        assert jaina.get(f"/v1/jobs/{child.job.id}").status_code == 404

    def test_losing_access_to_the_report_hides_its_job(self, h):
        jaina = h.actor("Jaina")
        h.wcl.readable[h.token_of("Jaina")] = {CODE}
        job_id = jaina.post("/v1/reports", json={"source": CODE}).json()["id"]
        h.wcl.readable.clear()
        h.clock.advance(hours=2)  # past the cached answer
        for url in (f"/v1/jobs/{job_id}", f"/v1/jobs/{job_id}/events"):
            assert jaina.get(url).json()["code"] == "report_not_found"

    def test_cancel_drops_a_queued_job(self, h):
        h.wcl.readable["app"] = {CODE}
        jaina = h.actor("Jaina")
        job_id = jaina.post("/v1/reports", json={"source": CODE}).json()["id"]
        resp = jaina.post(f"/v1/jobs/{job_id}/cancel")
        assert (resp.status_code, resp.json()["state"]) == (200, "cancelled")
        assert jaina.get(f"/v1/jobs/{job_id}").json()["state"] == "cancelled"

    def test_ingest_end_to_end_over_server_sent_events(self, h):
        jaina = h.actor("Jaina")
        h.wcl.readable[h.token_of("Jaina")] = {CODE}
        job = jaina.post("/v1/reports", json={"source": CODE}).json()
        worker = h.run_worker()
        with jaina.client.stream("GET", f"/v1/jobs/{job['id']}/events") as resp:
            assert resp.status_code == 200
            assert resp.headers["content-type"].startswith("text/event-stream")
            text = "".join(resp.iter_text())
        worker.join(timeout=60)
        events = sse_events(text)
        ids = [e[0] for e in events]
        names = [e[1] for e in events]
        assert ids == sorted(set(ids))  # increasing within the connection
        assert names[0] == "job.state"
        assert names[-1] == "job.done"
        assert "error" not in names
        final = events[-1][2]
        assert (final["state"], final["pct"], final["report_code"]) == ("done", 100.0, CODE)
        assert {p["pull_key"] for p in final["pulls"]} == {f"{CODE}:1", f"{CODE}:2"}
        assert all(p["status"] == "done" for p in final["pulls"])
        got = jaina.get(f"/v1/jobs/{job['id']}").json()
        assert got["state"] == "done" and got["error"] is None
        night = jaina.get(f"/v1/reports/{CODE}/night")  # the ingested report is readable now
        assert night.status_code == 200 and night.json()["sections"]

    def test_a_finished_job_streams_its_state_and_closes(self, h):
        jaina = h.actor("Jaina")
        h.wcl.readable[h.token_of("Jaina")] = {CODE}
        job = jaina.post("/v1/reports", json={"source": CODE}).json()
        h.run_worker().join(timeout=60)
        text = jaina.get(f"/v1/jobs/{job['id']}/events").text  # a reconnect after the end: current state, no replay
        assert [e[1] for e in sse_events(text)] == ["job.state", "job.done"]

    def test_a_failed_job_ends_with_a_typed_error_event(self, h):
        jaina = h.actor("Jaina")
        h.wcl.readable[h.token_of("Jaina")] = {"NOPAGES"}
        # An offline ingest of a report with no cached pages fails with not_in_cache.
        enq = asyncio.run(h.queue.enqueue(IngestReport(code="NOPAGES", offline=True), requested_by=jaina.user.id))
        h.run_worker().join(timeout=60)
        assert h.backend.rows[enq.job.id].state is JobState.FAILED
        events = sse_events(jaina.get(f"/v1/jobs/{enq.job.id}/events").text)
        assert [e[1] for e in events] == ["job.state", "error"]
        assert events[-1][2]["code"] == "not_in_cache"


class TestHostedApp:
    def test_jobs_need_auth(self, tmp_path):
        with pytest.raises(ValueError, match="hosted API"):
            create_app(mode="local", data_dir=tmp_path, jobs=object())  # type: ignore[arg-type]

    def test_the_contract_holds_every_hosted_route(self):
        from khadbot.api.app import hosted_documentation_app

        paths = hosted_documentation_app().openapi()["paths"]
        assert set(paths["/v1/reports"]) == {"get", "post"}
        assert {"/v1/jobs/{job_id}", "/v1/jobs/{job_id}/events", "/v1/jobs/{job_id}/cancel", "/v1/me"} <= set(paths)

    def test_local_mode_has_no_job_routes(self, tmp_path):
        client = TestClient(create_app(mode="local", data_dir=tmp_path))
        assert client.post("/v1/reports", json={"source": CODE}).status_code == 405
        assert client.get("/v1/jobs/x").status_code == 404
