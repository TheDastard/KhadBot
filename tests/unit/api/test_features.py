"""
tests/unit/api/test_features.py

``GET /v1/health`` says what the API offers: ``mode`` and one ``features`` flag per optional area, each true only
when that area's routes are mounted. Checked for local mode (no sign-in, hosted-only routes answer as unknown
routes), for the hosted API with the sign-in and job routers mounted, and for a partial deploy; each flag is compared
with whether its routes answer.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from helpers.api.hosted import Harness, build_harness
from helpers.jobs.budget import FakeReader, MovableClock, budget_config

from khadbot.api import create_app
from khadbot.api.schemas import Features
from khadbot.auth.routes import current_user, optional_user
from khadbot.auth.store import User
from khadbot.config import AuthConfig
from khadbot.jobs.budget_store import BudgetManager, MemoryBudgetBackend

ALL_OFF = {
    "sign_in": False,
    "ingest_jobs": False,
    "live_watch": False,
    "budget": False,
    "reviews": False,
    "chat": False,
}

# One request per flag whose route is mounted exactly when the flag is true. A route that is not mounted answers with
# the framework's own 404 (``{"detail": "Not Found"}``); a mounted one answers with the API's ``{code, message}`` body
# or a non-404 status.
PROBES = {
    "sign_in": ("GET", "/v1/me"),
    "ingest_jobs": ("GET", "/v1/jobs/nope"),
    "live_watch": ("GET", "/v1/reports/ABC123/watch"),
    "budget": ("GET", "/v1/budget"),
}


def answers(client: TestClient, method: str, path: str) -> bool:
    resp = client.request(method, path)
    return not (resp.status_code == 404 and resp.json() == {"detail": "Not Found"})


@pytest.fixture
def h(tmp_path, service, wcl, auth_store, clock) -> Harness:
    harness = build_harness(tmp_path, service, wcl, auth_store, clock)
    harness.app.state.budget = BudgetManager(
        MemoryBudgetBackend(), budget_config(), clock=MovableClock(), reader=FakeReader()
    )
    return harness


class FakeAuth:
    """Stands in for ``AuthService``: sign-in mounted without a jobs router."""

    config = AuthConfig()


class TestLocalMode:
    def test_reports_local_mode_with_every_feature_off(self, tmp_path):
        body = TestClient(create_app(mode="local", data_dir=tmp_path)).get("/v1/health").json()
        assert body["status"] == "ok"
        assert body["mode"] == "local"
        assert body["features"] == ALL_OFF

    def test_dataset_apps_are_local(self, client):
        body = client.get("/v1/health").json()
        assert (body["mode"], body["features"]) == ("local", ALL_OFF)

    @pytest.mark.parametrize("flag", PROBES)
    def test_hosted_only_routes_answer_404_like_unknown_routes(self, tmp_path, flag):
        client = TestClient(create_app(mode="local", data_dir=tmp_path))
        method, path = PROBES[flag]
        assert client.request(method, path).json() == {"detail": "Not Found"}
        assert client.get("/v1/no-such-route").json() == {"detail": "Not Found"}

    def test_serves_reads_without_sign_in(self, tmp_path):
        client = TestClient(create_app(mode="local", data_dir=tmp_path))
        assert client.get("/v1/reports").json()["total"] == 0

    def test_cors_allows_the_web_dev_origins_for_get(self, tmp_path):
        client = TestClient(create_app(mode="local", data_dir=tmp_path))
        for origin in ("http://localhost:3000", "http://127.0.0.1:3000"):
            resp = client.get("/v1/health", headers={"Origin": origin})
            assert resp.headers["access-control-allow-origin"] == origin
            assert "access-control-allow-credentials" not in resp.headers


class TestHostedMode:
    def test_reports_hosted_mode_with_what_is_mounted(self, h):
        body = h.actor().get("/v1/health").json()
        assert body["mode"] == "hosted"
        assert body["features"] == {**ALL_OFF, "sign_in": True, "ingest_jobs": True, "live_watch": True, "budget": True}

    @pytest.mark.parametrize("flag", PROBES)
    def test_every_flag_matches_whether_its_routes_answer(self, h, flag):
        visitor = h.actor()
        flags = visitor.get("/v1/health").json()["features"]
        method, path = PROBES[flag]
        assert flags[flag] is True
        assert answers(visitor.client, method, path) is flags[flag]

    def test_health_is_open_to_visitors(self, h):
        assert h.actor().get("/v1/health").status_code == 200


class TestPartialDeploy:
    def test_sign_in_without_a_job_queue_reports_no_ingest_jobs(self, tmp_path):
        user = User(id="u1", wcl_user_id=42, name="Tester")
        app = create_app(mode="hosted", data_dir=tmp_path, auth=FakeAuth())  # type: ignore[arg-type]
        app.dependency_overrides[optional_user] = lambda: user
        app.dependency_overrides[current_user] = lambda: user
        client = TestClient(app)
        body = client.get("/v1/health").json()
        assert body["mode"] == "hosted"
        assert body["features"] == {**ALL_OFF, "sign_in": True, "budget": True}
        assert answers(client, *PROBES["ingest_jobs"]) is False  # jobs not mounted: flag and route agree

    def test_live_watch_needs_the_job_queue_like_its_routes(self, tmp_path):
        app = create_app(mode="hosted", data_dir=tmp_path, auth=FakeAuth())  # type: ignore[arg-type]
        client = TestClient(app)
        assert client.get("/v1/health").json()["features"]["live_watch"] is False
        assert answers(client, *PROBES["live_watch"]) is False


class TestModeIsTheCallers:
    """``mode`` is a label from the entry point; what is mounted never changes it."""

    def test_the_label_is_whatever_the_caller_gave(self, tmp_path):
        local = TestClient(create_app(mode="local", data_dir=tmp_path))
        assert local.get("/v1/health").json()["mode"] == "local"
        hosted = TestClient(create_app(mode="hosted", data_dir=tmp_path, auth=FakeAuth()))  # type: ignore[arg-type]
        assert hosted.get("/v1/health").json()["mode"] == "hosted"

    def test_the_environment_does_not_change_it(self, tmp_path, monkeypatch):
        monkeypatch.setenv("KHADBOT_MODE", "hosted")
        body = TestClient(create_app(mode="local", data_dir=tmp_path)).get("/v1/health").json()
        assert (body["mode"], body["features"]) == ("local", ALL_OFF)

    @pytest.mark.parametrize(
        ("mode", "auth"),
        [("hosted", None), ("local", FakeAuth())],
        ids=["hosted-without-sign-in", "local-with-sign-in"],
    )
    def test_a_label_that_contradicts_the_services_is_refused(self, tmp_path, mode, auth):
        with pytest.raises(ValueError, match="does not fit"):
            create_app(mode=mode, data_dir=tmp_path, auth=auth)  # type: ignore[arg-type]

    def test_the_documentation_app_is_hosted(self, tmp_path):
        from khadbot.api.app import hosted_documentation_app

        body = TestClient(hosted_documentation_app(tmp_path)).get("/v1/health").json()
        assert body["mode"] == "hosted"
        assert body["features"] == {**ALL_OFF, "sign_in": True, "ingest_jobs": True, "live_watch": True, "budget": True}


def test_features_model_has_one_flag_per_area():
    assert set(Features().model_dump()) == set(ALL_OFF)
