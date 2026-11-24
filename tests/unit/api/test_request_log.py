"""
tests/unit/api/test_request_log.py

The request log middleware through the app: every response carries an ``X-Request-ID`` (a valid incoming one kept, an
invalid one replaced and never echoed) exposed through CORS, and each request writes one ``http.request`` line with
that ID, the route template, status, duration, the signed-in user and the error code. Lines a handler logs carry the
request ID and, once the user is known, the user ID. An unhandled exception answers a JSON 500 that still carries the
request ID and CORS headers. Lines are captured as JSON from ``configure_logging``.
"""

from __future__ import annotations

import io
import json
import logging
import re

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from helpers.api.hosted import Harness, build_harness

from khadbot.api import create_app
from khadbot.api.limits import RequestLimits
from khadbot.api.request_log import INTERNAL_ERROR_MESSAGE, RequestLog
from khadbot.auth.routes import OptionalUser
from khadbot.config import LoggingConfig
from khadbot.logging_setup import configure_logging

CODE = "SYNTH01"
WEB_ORIGIN = "http://localhost:3000"
GENERATED = re.compile(r"^[0-9a-f]{16}$")


@pytest.fixture
def stream():
    buffer = io.StringIO()
    handler = configure_logging(LoggingConfig(log_format="json", log_level="DEBUG"), stream=buffer)
    yield buffer
    logging.getLogger().removeHandler(handler)


def lines(stream: io.StringIO) -> list[dict]:
    return [json.loads(line) for line in stream.getvalue().splitlines()]


def request_lines(stream: io.StringIO) -> list[dict]:
    return [line for line in lines(stream) if line.get("event") == "http.request"]


def only_request_line(stream: io.StringIO) -> dict:
    found = request_lines(stream)
    assert len(found) == 1, found
    return found[0]


probe = APIRouter()


@probe.get("/v1/probe/{name}")
async def _probe(name: str) -> dict:
    logging.getLogger("khadbot.test.probe").info("inside the handler for %s", name)
    return {"name": name}


@probe.get("/v1/boom")
async def _boom() -> dict:
    raise RuntimeError("the handler broke")


# Handlers that log after the user is resolved: one async, one sync (run in the thread pool).
signed = APIRouter()


@signed.get("/v1/probe-user/async")
async def _probe_user_async(user: OptionalUser) -> dict:
    logging.getLogger("khadbot.test.probe").info("async handler")
    return {}


@signed.get("/v1/probe-user/sync")
def _probe_user_sync(user: OptionalUser) -> dict:
    logging.getLogger("khadbot.test.probe").info("sync handler")
    return {}


def probe_lines(stream: io.StringIO) -> list[dict]:
    return [line for line in lines(stream) if line["logger"] == "khadbot.test.probe"]


@pytest.fixture
def local(tmp_path) -> TestClient:
    # Server exceptions would raise in the test: none should get past RequestLog.
    return TestClient(create_app(mode="local", data_dir=tmp_path, routers=[probe]))


@pytest.fixture
def h(tmp_path, service, wcl, auth_store, clock) -> Harness:
    return build_harness(tmp_path, service, wcl, auth_store, clock)


# ---------------------------------------------------------------------------
# Request IDs
# ---------------------------------------------------------------------------


def test_a_request_gets_an_id_and_one_line_with_route_status_and_duration(local, stream):
    resp = local.get("/v1/probe/thrall")
    rid = resp.headers["x-request-id"]
    assert GENERATED.match(rid)
    line = only_request_line(stream)
    assert line["request_id"] == rid
    assert (line["level"], line["logger"]) == ("INFO", "khadbot.api.request_log")
    assert line["message"] == "GET /v1/probe/{name} 200"
    assert (line["method"], line["route"], line["status"]) == ("GET", "/v1/probe/{name}", 200)
    assert isinstance(line["duration_ms"], int) and line["duration_ms"] >= 0
    assert "user_id" not in line and "error_code" not in line


def test_a_line_logged_inside_the_handler_carries_the_request_id(local, stream):
    rid = local.get("/v1/probe/jaina").headers["x-request-id"]
    inside = [line for line in lines(stream) if line["logger"] == "khadbot.test.probe"]
    assert [line["request_id"] for line in inside] == [rid]


def test_each_request_gets_its_own_id(local, stream):
    first = local.get("/v1/probe/a").headers["x-request-id"]
    second = local.get("/v1/probe/b").headers["x-request-id"]
    assert first != second
    assert [line["request_id"] for line in request_lines(stream)] == [first, second]


def test_a_valid_incoming_id_is_reused(local, stream):
    resp = local.get("/v1/probe/a", headers={"X-Request-ID": "web-7f3a9c0e-5b2d"})
    assert resp.headers["x-request-id"] == "web-7f3a9c0e-5b2d"
    assert only_request_line(stream)["request_id"] == "web-7f3a9c0e-5b2d"


@pytest.mark.parametrize("bad", ["short", "x" * 65, "has spaces in it", "semi;colon-1234", "<script>alert</script>"])
def test_an_invalid_incoming_id_is_replaced_and_never_echoed(local, stream, bad):
    resp = local.get("/v1/probe/a", headers={"X-Request-ID": bad})
    rid = resp.headers["x-request-id"]
    assert rid != bad and GENERATED.match(rid)
    assert resp.headers.get_list("x-request-id") == [rid]
    assert bad not in stream.getvalue()


# ---------------------------------------------------------------------------
# Routes, levels and errors
# ---------------------------------------------------------------------------


def test_an_unmatched_path_logs_route_unmatched(local, stream):
    resp = local.get("/wp-admin/setup-config.php")
    assert resp.status_code == 404
    assert "x-request-id" in resp.headers
    line = only_request_line(stream)
    assert (line["route"], line["status"]) == ("unmatched", 404)
    assert "wp-admin" not in json.dumps(line)  # the raw path never reaches the line


def test_health_logs_at_debug(local, stream):
    local.get("/v1/health")
    line = only_request_line(stream)
    assert (line["level"], line["route"]) == ("DEBUG", "/v1/health")


def test_a_validation_error_carries_bad_request(local, stream):
    resp = local.get(f"/v1/reports/{CODE}/pulls/1/events")
    assert resp.status_code == 422
    line = only_request_line(stream)
    assert (line["status"], line["error_code"], line["level"]) == (422, "bad_request", "INFO")


def test_an_unhandled_exception_answers_a_json_500_with_the_request_id_and_one_line(local, stream):
    resp = local.get("/v1/boom")
    assert resp.status_code == 500
    assert resp.json() == {"code": "internal_error", "message": INTERNAL_ERROR_MESSAGE}
    assert resp.headers["content-type"] == "application/json"
    line = only_request_line(stream)
    assert (line["level"], line["status"], line["route"]) == ("WARNING", 500, "/v1/boom")
    assert line["error_code"] == "internal_error"
    assert "the handler broke" in line["exc"]
    assert line["request_id"] == resp.headers["x-request-id"]
    assert [entry for entry in lines(stream) if "exc" in entry] == [line]  # logged once


async def test_an_exception_after_the_response_started_is_logged_and_re_raised(stream):
    sent: list[dict] = []

    async def half_answer(scope, receive, send) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": []})
        raise RuntimeError("broke mid-stream")

    async def receive() -> dict:
        return {"type": "http.request", "body": b""}

    async def send(message) -> None:
        sent.append(message)

    scope = {"type": "http", "method": "GET", "path": "/v1/stream", "headers": []}
    with pytest.raises(RuntimeError, match="broke mid-stream"):
        await RequestLog(half_answer)(scope, receive, send)
    assert [m["type"] for m in sent] == ["http.response.start"]  # no second answer
    line = only_request_line(stream)
    assert line["status"] == 500 and "broke mid-stream" in line["exc"]
    assert "error_code" not in line


def test_a_rate_limited_request_carries_its_error_code(stream):
    app = FastAPI()

    @app.get("/v1/thing")
    def thing() -> dict:
        return {}

    app.add_middleware(RequestLimits, max_body_bytes=0, per_minute=1)
    app.add_middleware(RequestLog)
    client = TestClient(app)
    client.get("/v1/thing")
    resp = client.get("/v1/thing")
    assert resp.status_code == 429
    assert "x-request-id" in resp.headers
    last = request_lines(stream)[-1]
    assert (last["status"], last["error_code"], last["route"]) == (429, "rate_limited", "unmatched")


# ---------------------------------------------------------------------------
# Hosted: the user, API errors, CORS
# ---------------------------------------------------------------------------


def test_a_signed_in_request_carries_the_user_id(h, stream):
    jaina = h.actor("Jaina")
    assert jaina.get("/v1/me").status_code == 200
    line = only_request_line(stream)
    assert line["user_id"] == jaina.user.id
    assert line["route"] == "/v1/me"


def test_a_visitor_request_has_no_user_id(h, stream):
    h.actor().get("/v1/cooldowns")
    assert "user_id" not in only_request_line(stream)


@pytest.mark.parametrize("kind", ["async", "sync"])
def test_lines_a_handler_logs_carry_the_signed_in_user(h, stream, kind):
    h.app.include_router(signed)
    jaina = h.actor("Jaina")
    stream.seek(0)
    stream.truncate()
    resp = jaina.get(f"/v1/probe-user/{kind}")
    assert resp.status_code == 200
    (inside,) = probe_lines(stream)
    assert (inside["user_id"], inside["request_id"]) == (jaina.user.id, resp.headers["x-request-id"])
    assert only_request_line(stream)["user_id"] == jaina.user.id


def test_a_visitors_handler_lines_have_no_user_and_nothing_carries_over(h, stream):
    h.app.include_router(signed)
    jaina = h.actor("Jaina")
    visitor = h.actor()
    stream.seek(0)
    stream.truncate()
    jaina.get("/v1/probe-user/async")
    visitor.get("/v1/probe-user/async")
    visitor.get("/v1/probe-user/sync")
    first, *later = probe_lines(stream)
    assert first["user_id"] == jaina.user.id
    assert len(later) == 2 and all("user_id" not in line and line["request_id"] for line in later)
    assert ["user_id" in line for line in request_lines(stream)] == [True, False, False]


def test_an_auth_error_carries_its_code(h, stream):
    resp = h.actor().get("/v1/me")
    assert resp.status_code == 401
    line = only_request_line(stream)
    assert (line["error_code"], line["level"]) == ("not_signed_in", "INFO")


def test_wcl_unavailable_logs_warning_with_the_error_code(h, stream):
    import httpx

    from khadbot.wcl.client import GRAPHQL_CLIENT_URL

    h.ingest_offline()
    h.wcl.router.post(GRAPHQL_CLIENT_URL).mock(side_effect=httpx.ConnectError("down"))
    stream.seek(0)
    stream.truncate()
    resp = h.actor().get(f"/v1/reports/{CODE}/night")
    assert (resp.status_code, resp.json()["code"]) == (503, "wcl_unavailable")
    line = request_lines(stream)[-1]
    assert (line["level"], line["status"], line["error_code"]) == ("WARNING", 503, "wcl_unavailable")
    assert line["route"] == "/v1/reports/{code}/night"
    assert line["request_id"] == resp.headers["x-request-id"]


def test_hosted_cors_exposes_the_request_id(h):
    resp = h.actor().client.get("/v1/health", headers={"Origin": WEB_ORIGIN})
    assert resp.headers["access-control-allow-origin"] == WEB_ORIGIN
    assert "x-request-id" in resp.headers["access-control-expose-headers"].lower()
    assert resp.headers["x-request-id"]


def test_hosted_error_answers_carry_cors_and_the_request_id(h):
    resp = h.actor().client.get("/v1/me", headers={"Origin": WEB_ORIGIN})
    assert resp.status_code == 401
    assert resp.headers["access-control-allow-origin"] == WEB_ORIGIN
    assert resp.headers["x-request-id"]


def test_hosted_unhandled_exception_answers_with_cors_and_the_request_id(h, stream):
    h.app.include_router(probe)
    resp = h.actor().client.get("/v1/boom", headers={"Origin": WEB_ORIGIN})
    assert (resp.status_code, resp.json()["code"]) == (500, "internal_error")
    assert resp.headers["access-control-allow-origin"] == WEB_ORIGIN
    assert "x-request-id" in resp.headers["access-control-expose-headers"].lower()
    line = only_request_line(stream)
    assert line["request_id"] == resp.headers["x-request-id"]
    assert "the handler broke" in line["exc"]


def test_local_cors_exposes_the_request_id(local):
    resp = local.get("/v1/health", headers={"Origin": WEB_ORIGIN})
    assert "x-request-id" in resp.headers["access-control-expose-headers"].lower()
