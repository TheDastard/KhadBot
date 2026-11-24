"""
tests/unit/test_error_reporting.py

Error reporting through the Sentry SDK: off and never imported without a DSN; with one, every event leaves redacted
and tagged with the service and the log context's IDs. The SDK patches libraries process-wide when it starts, so the
end-to-end cases run in a child interpreter with warnings as errors, and send to a fake transport, never the network.
All the secrets here are obviously fake.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap

import pytest

from khadbot.config import ErrorReportingConfig
from khadbot.error_reporting import init_error_reporting, report_exception, scrub_breadcrumb, scrub_event
from khadbot.logging_setup import REDACTED, log_context

FAKE_DSN = "https://public@errors.example.invalid/1"
SECRET_MESSAGE = "WCL call failed with Bearer abc.def.ghi and password=hunter2"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def run_child(script: str, **env: str) -> subprocess.CompletedProcess[str]:
    """Run ``script`` in a fresh interpreter with warnings as errors and only ``env`` set among the Sentry settings."""
    child_env = {k: v for k, v in os.environ.items() if not k.startswith(("SENTRY_", "KHADBOT_VERSION", "KHADBOT_ENV"))}
    child_env.update(env)
    return subprocess.run(
        [sys.executable, "-W", "error::DeprecationWarning", "-W", "error::PendingDeprecationWarning", "-c", script],
        capture_output=True,
        text=True,
        env=child_env,
        timeout=120,
        check=False,
    )


# ---------------------------------------------------------------------------
# Off without a DSN
# ---------------------------------------------------------------------------


def test_without_a_dsn_nothing_starts_and_the_sdk_is_never_imported():
    script = textwrap.dedent(
        """
        import sys
        from khadbot.error_reporting import init_error_reporting
        started = init_error_reporting("api")
        print(started, "sentry_sdk" in sys.modules)
        """
    )
    result = run_child(script, SENTRY_DSN="")
    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == ["False", "False"]


def test_config_is_off_by_default_and_checks_the_sample_rate(monkeypatch):
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    monkeypatch.delenv("SENTRY_TRACES_SAMPLE_RATE", raising=False)
    cfg = ErrorReportingConfig()
    assert not cfg.enabled and cfg.traces_sample_rate == 0
    assert init_error_reporting("api", cfg) is False
    assert FAKE_DSN not in repr(ErrorReportingConfig(dsn=FAKE_DSN))
    with pytest.raises(ValueError, match="SENTRY_TRACES_SAMPLE_RATE"):
        ErrorReportingConfig(dsn=FAKE_DSN, traces_sample_rate=2)


def test_a_dsn_without_the_sdk_names_the_hosted_group(monkeypatch):
    monkeypatch.setitem(sys.modules, "sentry_sdk", None)
    with pytest.raises(ImportError, match="'hosted' dependency group"):
        init_error_reporting("api", ErrorReportingConfig(dsn=FAKE_DSN))


# ---------------------------------------------------------------------------
# Scrubbing
# ---------------------------------------------------------------------------


def test_scrub_event_redacts_every_string_drops_request_secrets_and_tags_the_context():
    event = {
        "message": SECRET_MESSAGE,
        "logentry": {"message": "token=%s", "params": ["tok-FAKE"], "formatted": "token=tok-FAKE"},
        "exception": {"values": [{"type": "RuntimeError", "value": SECRET_MESSAGE}]},
        "breadcrumbs": {"values": [{"message": "Authorization: Bearer xyz.abc"}]},
        "request": {
            "url": "https://api.example.invalid/v1/auth/callback",
            "query_string": "code=fakeoauthcode456&state=s1",
            "cookies": {"khadbot_session": "FAKE"},
            "headers": {"Cookie": "khadbot_session=FAKE"},
            "data": {"password": "hunter2"},
        },
        "tags": {"existing": "kept"},
    }
    with log_context(request_id="req-1", job_id="job-7"):
        sent = scrub_event(event, "api")
    text = json.dumps(sent)
    for secret in ("abc.def.ghi", "hunter2", "tok-FAKE", "xyz.abc", "fakeoauthcode456", "khadbot_session"):
        assert secret not in text
    assert REDACTED in sent["exception"]["values"][0]["value"]
    assert set(sent["request"]) == {"url", "query_string"}
    assert sent["tags"] == {"existing": "kept", "service": "api", "request_id": "req-1", "job_id": "job-7"}


def test_scrub_breadcrumb_redacts():
    crumb = scrub_breadcrumb({"category": "httpx", "data": {"url": "postgres://khadbot:hunter2@db/khadbot"}})
    assert "hunter2" not in json.dumps(crumb)


# ---------------------------------------------------------------------------
# End to end with a fake transport
# ---------------------------------------------------------------------------

CAPTURE_SCRIPT = textwrap.dedent(
    """
    import json, logging, sys
    import sentry_sdk
    from sentry_sdk.transport import Transport
    from khadbot.error_reporting import init_error_reporting
    from khadbot.logging_setup import configure_logging, log_context

    EVENTS = []

    class Capture(Transport):
        def capture_envelope(self, envelope):
            event = envelope.get_event()
            if event is not None:
                EVENTS.append(event)

    configure_logging(service="worker", stream=sys.stderr)  # as main.py does, before error reporting starts
    assert init_error_reporting("worker", transport=Capture)
    log = logging.getLogger("khadbot.test")
    with log_context(request_id="req-1", job_id="job-7"):
        log.info("calling WCL with password=hunter2")  # a breadcrumb
        try:
            raise RuntimeError(SECRET_MESSAGE)
        except RuntimeError:
            sentry_sdk.capture_exception()
        try:
            raise ValueError("no pages")  # a second exception: the SDK drops a repeat of one it already sent
        except ValueError:
            log.exception("job failed: token=%s", "tok-FAKE-0123")
    sentry_sdk.flush()
    print(json.dumps(EVENTS, default=str))
    """.replace("SECRET_MESSAGE", repr(SECRET_MESSAGE))
)


API_SCRIPT = textwrap.dedent(
    """
    import json, sys
    import sentry_sdk
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sentry_sdk.transport import Transport
    from khadbot.api.request_log import RequestLog
    from khadbot.error_reporting import init_error_reporting
    from khadbot.logging_setup import configure_logging

    EVENTS = []

    class Capture(Transport):
        def capture_envelope(self, envelope):
            event = envelope.get_event()
            if event is not None:
                EVENTS.append(event)

    configure_logging(service="api", stream=sys.stderr)
    assert init_error_reporting("api", transport=Capture)
    app = FastAPI()

    @app.get("/v1/boom")
    async def boom():
        raise RuntimeError("the handler broke with password=hunter2")

    app.add_middleware(RequestLog)
    resp = TestClient(app).get("/v1/boom")
    sentry_sdk.flush()
    print(json.dumps({"status": resp.status_code, "rid": resp.headers["x-request-id"], "events": EVENTS}, default=str))
    """
)


def test_an_unhandled_api_exception_answered_by_the_request_log_still_arrives_once():
    result = run_child(API_SCRIPT, SENTRY_DSN=FAKE_DSN)
    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout.strip().splitlines()[-1])
    assert out["status"] == 500
    (event,) = out["events"]
    assert event["exception"]["values"][-1]["value"] == f"the handler broke with password={REDACTED}"
    assert event["tags"]["request_id"] == out["rid"]
    assert event["tags"]["service"] == "api"


def test_report_exception_does_nothing_without_the_sdk(monkeypatch):
    monkeypatch.delitem(sys.modules, "sentry_sdk", raising=False)
    report_exception(RuntimeError("x"))
    assert "sentry_sdk" not in sys.modules


def test_a_raised_exception_arrives_redacted_and_tagged():
    result = run_child(CAPTURE_SCRIPT, SENTRY_DSN=FAKE_DSN, KHADBOT_VERSION="abc1234", KHADBOT_ENV="stack")
    assert result.returncode == 0, result.stderr
    events = json.loads(result.stdout.strip().splitlines()[-1])
    assert len(events) == 2  # capture_exception, then the logged exception
    text = json.dumps(events)
    for secret in ("abc.def.ghi", "hunter2", "tok-FAKE-0123"):
        assert secret not in text
    captured, logged = events
    assert captured["exception"]["values"][-1]["value"] == (
        f"WCL call failed with Bearer {REDACTED} and password={REDACTED}"
    )
    assert logged["logentry"]["formatted"] == f"job failed: token={REDACTED}"
    assert "params" not in logged["logentry"]
    for event in events:
        assert event["tags"]["service"] == "worker"
        assert event["tags"]["request_id"] == "req-1"
        assert event["tags"]["job_id"] == "job-7"
        assert event["release"] == "abc1234"
        assert event["environment"] == "stack"
    crumbs = [c.get("message", "") for c in captured.get("breadcrumbs", {}).get("values", [])]
    assert any(c.startswith("calling WCL with password=") for c in crumbs)
