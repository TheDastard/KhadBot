"""
tests/unit/api/test_limits.py

``RequestLimits``: the body-size cap (declared and streamed bodies) and the per-client request rate, on a small app.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from khadbot.api.limits import RequestLimits
from khadbot.config import ApiConfig


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class Body(BaseModel):
    text: str


def build(*, max_body_bytes: int = 0, per_minute: int = 0, clock: Clock | None = None) -> TestClient:
    app = FastAPI()

    @app.get("/v1/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.get("/v1/thing")
    def thing() -> dict:
        return {"ok": True}

    @app.post("/v1/thing")
    def post_thing(body: Body) -> dict:
        return {"length": len(body.text)}

    app.add_middleware(RequestLimits, max_body_bytes=max_body_bytes, per_minute=per_minute, clock=clock or Clock())
    return TestClient(app)


def test_a_small_body_passes():
    assert build(max_body_bytes=1000).post("/v1/thing", json={"text": "hello"}).json() == {"length": 5}


def test_a_declared_body_over_the_limit_is_413_before_it_is_read():
    resp = build(max_body_bytes=100).post("/v1/thing", json={"text": "x" * 500})
    assert resp.status_code == 413 and resp.json()["code"] == "body_too_large"


def test_a_streamed_body_over_the_limit_is_413():
    def chunks():
        yield b'{"text": "'
        for _ in range(50):
            yield b"x" * 20
        yield b'"}'

    client = build(max_body_bytes=100)
    resp = client.post("/v1/thing", content=chunks(), headers={"content-type": "application/json"})
    assert "content-length" not in resp.request.headers  # sent chunked: only counting can catch it
    assert resp.status_code == 413 and resp.json()["code"] == "body_too_large"


def test_no_limit_accepts_any_body():
    assert build().post("/v1/thing", json={"text": "x" * 5000}).status_code == 200


def test_requests_over_the_rate_are_429_until_the_window_ends():
    clock = Clock()
    client = build(per_minute=3, clock=clock)
    assert [client.get("/v1/thing").status_code for _ in range(5)] == [200, 200, 200, 429, 429]
    refused = client.get("/v1/thing")
    assert refused.json()["code"] == "rate_limited" and int(refused.headers["retry-after"]) >= 1
    clock.now += 61
    assert client.get("/v1/thing").status_code == 200


def test_health_and_preflights_are_not_counted():
    client = build(per_minute=1)
    assert client.get("/v1/thing").status_code == 200
    assert [client.get("/v1/health").status_code for _ in range(5)] == [200] * 5
    assert client.options("/v1/thing").status_code != 429
    assert client.get("/v1/thing").status_code == 429


def test_each_client_address_has_its_own_count():
    clock = Clock()
    app = FastAPI()

    @app.get("/v1/thing")
    def thing() -> dict:
        return {"ok": True}

    app.add_middleware(RequestLimits, max_body_bytes=0, per_minute=1, clock=clock)
    one = TestClient(app, client=("203.0.113.1", 5000))
    two = TestClient(app, client=("203.0.113.2", 5000))
    assert (one.get("/v1/thing").status_code, one.get("/v1/thing").status_code) == (200, 429)
    assert two.get("/v1/thing").status_code == 200


def test_the_rate_limit_is_on_by_default_only_off_localhost(monkeypatch):
    monkeypatch.delenv("KHADBOT_RATE_LIMIT_PER_MIN", raising=False)
    assert ApiConfig().rate_limit(local=True) == 0  # the dev loop and the end-to-end tests
    assert ApiConfig().rate_limit(local=False) == ApiConfig.DEFAULT_RATE_LIMIT_PER_MIN
    monkeypatch.setenv("KHADBOT_RATE_LIMIT_PER_MIN", "40")
    assert (ApiConfig().rate_limit(local=True), ApiConfig().rate_limit(local=False)) == (40, 40)
    monkeypatch.setenv("KHADBOT_RATE_LIMIT_PER_MIN", "0")
    assert ApiConfig().rate_limit(local=False) == 0
