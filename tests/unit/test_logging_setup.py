"""
tests/unit/test_logging_setup.py

The hosted log format: one JSON object per line with the job, user and report IDs as context, and a log-capture test
that no secret-shaped value reaches the output however it is logged (message, argument, URL, exception, header).
All the secrets here are obviously fake.
"""

from __future__ import annotations

import base64
import io
import json
import logging

import pytest

from khadbot.config import LoggingConfig
from khadbot.logging_setup import configure_logging, log_context, redact

FERNET_KEY = base64.urlsafe_b64encode(
    b"fake-key-0123456789abcdef-012345"
).decode()  # Fernet-shaped: 32 bytes, 44 characters
SECRETS = {
    "access token": "tok-live-FAKE-0123456789abcdef",
    "refresh token": "refresh-FAKE-9876543210zyxwvu",
    "session cookie": "FAKESESSIONCOOKIEVALUE0123456789abcdefghij",
    "client secret": "fake-client-secret-value-123",
    "database password": "fake-db-password-xyz",
    "fernet key": FERNET_KEY,
    "oauth code": "fakeoauthcode456",
    "api key": "sk-ant-FAKEKEYFAKEKEYFAKEKEY0123",
    "jwt": "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJmYWtlIn0.fakeSignature123",
}


@pytest.fixture
def stream():
    buffer = io.StringIO()
    handler = configure_logging(LoggingConfig(log_format="json", log_level="INFO"), stream=buffer)
    yield buffer
    logging.getLogger().removeHandler(handler)


def lines(stream: io.StringIO) -> list[dict]:
    return [json.loads(line) for line in stream.getvalue().splitlines()]


def test_a_record_is_one_json_line_with_the_context_ids(stream):
    log = logging.getLogger("khadbot.test")
    with log_context(job_id="job1", user_id="user1", report_code="ABC123"):
        log.info("Job %s fetched %d pulls", "job1", 3)
    log.info("outside")

    inside, outside = lines(stream)
    assert inside["message"] == "Job job1 fetched 3 pulls" and inside["level"] == "INFO"
    assert (inside["job_id"], inside["user_id"], inside["report_code"]) == ("job1", "user1", "ABC123")
    assert inside["logger"] == "khadbot.test" and "time" in inside
    assert "job_id" not in outside  # the context ends with the block


def test_the_context_takes_ids_only():
    with pytest.raises(ValueError):
        with log_context(access_token="x"):  # type: ignore[arg-type]
            pass


def test_no_secret_shaped_value_is_logged(stream):
    log = logging.getLogger("khadbot.test")
    s = SECRETS
    log.info("Refreshing with access_token=%s", s["access token"])
    log.info("Response: {'refresh_token': '%s', 'expires_in': 3600}", s["refresh token"])
    log.info('{"client_secret": "%s"}', s["client secret"])
    log.warning("Headers: Cookie: khadbot_session=%s; theme=dark", s["session cookie"])
    log.warning("Set-Cookie: khadbot_session=%s; HttpOnly; Secure", s["session cookie"])
    log.warning("Authorization: Bearer %s", s["access token"])
    log.warning("Bearer %s was rejected", s["access token"])
    log.error("connect failed: postgresql://khadbot:%s@db.internal:5432/khadbot", s["database password"])
    log.error("conninfo host=db user=khadbot password=%s dbname=khadbot", s["database password"])
    log.info("Encryption key is %s", s["fernet key"])
    log.info("GET /v1/auth/callback?code=%s&state=fakestate789 200", s["oauth code"])
    log.info("Calling the model with %s", s["api key"])
    log.info("Decoded %s", s["jwt"])
    try:
        raise RuntimeError(f"could not connect to postgresql://user:{s['database password']}@host/db")
    except RuntimeError:
        log.exception("Job failed with secret=%s", s["client secret"])

    output = stream.getvalue()
    for name, value in s.items():
        assert value not in output, f"the {name} was logged"
    assert "<redacted>" in output


def test_redaction_keeps_ids_and_token_counts():
    text = "job j1 user u1 report ABC123 input_tokens=1200 output_tokens: 80 points_spent=15.5 host=db.internal"
    assert redact(text) == text


def test_text_format_redacts_too():
    buffer = io.StringIO()
    handler = configure_logging(LoggingConfig(log_format="text", log_level="INFO"), stream=buffer)
    try:
        with log_context(job_id="job1"):
            logging.getLogger("khadbot.test").info("token=%s", SECRETS["access token"])
    finally:
        logging.getLogger().removeHandler(handler)
    out = buffer.getvalue()
    assert SECRETS["access token"] not in out and "[job_id=job1]" in out


def test_configuring_twice_leaves_one_handler():
    first = configure_logging(LoggingConfig(log_format="json"), stream=io.StringIO())
    second = configure_logging(LoggingConfig(log_format="json"), stream=io.StringIO())
    try:
        assert first not in logging.getLogger().handlers and second in logging.getLogger().handlers
    finally:
        logging.getLogger().removeHandler(second)
