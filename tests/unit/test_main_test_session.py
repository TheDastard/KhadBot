"""
tests/unit/test_main_test_session.py

The ``test-session`` subcommand: parsing, the gate (checked before a database is opened), the cookie on stdout and
nothing else there, the Playwright storage-state file, and the development-switch log line at API and worker start.
The Postgres store is replaced by the in-memory one; no database is involved.
"""

from __future__ import annotations

import json
import logging

import pytest

from khadbot.auth.store import MemoryAuthStore
from khadbot.main import _log_development_switches, build_parser, main


@pytest.fixture
def hosted(monkeypatch):
    """Hosted settings with the Postgres store and pool replaced by an in-memory store."""
    store = MemoryAuthStore()
    monkeypatch.setenv("KHADBOT_MODE", "hosted")
    monkeypatch.setenv("DATABASE_URL", "postgresql://fake:fake@localhost/fake")
    monkeypatch.setattr("khadbot.db.get_pool", lambda: object())
    closed: list[bool] = []
    monkeypatch.setattr("khadbot.db.close_pool", lambda: closed.append(True))
    store.pool_closed = closed  # type: ignore[attr-defined]
    monkeypatch.setattr("khadbot.auth.pg.PostgresAuthStore", lambda pool: store)
    return store


def test_parses_arguments():
    args = build_parser().parse_args(["test-session", "--user", "bob", "--reports", "A1", "B2", "--hours", "2"])
    assert (args.user, args.reports, args.hours, args.storage_state) == ("bob", ["A1", "B2"], 2, None)


def test_refuses_when_the_gate_is_off_before_opening_a_database(monkeypatch, capsys):
    monkeypatch.delenv("KHADBOT_TEST_SESSIONS", raising=False)

    def boom():
        raise AssertionError("the database was opened")

    monkeypatch.setattr("khadbot.db.get_pool", boom)
    with pytest.raises(SystemExit) as exc:
        main(["test-session"])
    assert "KHADBOT_TEST_SESSIONS" in str(exc.value)
    assert capsys.readouterr().out == ""


def test_needs_the_hosted_stack(monkeypatch):
    monkeypatch.setenv("KHADBOT_TEST_SESSIONS", "1")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("KHADBOT_MODE", raising=False)
    with pytest.raises(SystemExit, match="hosted"):
        main(["test-session"])


def test_prints_only_the_cookie_on_stdout(monkeypatch, hosted, capsys):
    monkeypatch.setenv("KHADBOT_TEST_SESSIONS", "1")
    main(["test-session", "--user", "alice", "--reports", "AbC123"])
    out, err = capsys.readouterr()
    (session,) = hosted.sessions.values()
    assert out.strip() and "\n" not in out.strip() and out.strip() not in err
    assert session.id_hash != out.strip()  # only a digest is stored
    assert "Test alice" in err and "1 reports readable" in err


def test_writes_a_storage_state_file_and_prints_no_cookie(monkeypatch, hosted, capsys, tmp_path):
    monkeypatch.setenv("KHADBOT_TEST_SESSIONS", "1")
    target = tmp_path / "alice.json"
    main(["test-session", "--user", "alice", "--storage-state", str(target), "--domain", "127.0.0.1"])
    out, err = capsys.readouterr()
    (cookie,) = json.loads(target.read_text(encoding="utf-8"))["cookies"]
    assert out == "" and cookie["value"] not in err
    assert (cookie["name"], cookie["domain"], cookie["httpOnly"]) == ("khadbot_session", "127.0.0.1", True)
    assert cookie["secure"] is False  # the default redirect URI is plain-HTTP localhost


def test_a_bad_name_is_a_clean_exit(monkeypatch, hosted):
    monkeypatch.setenv("KHADBOT_TEST_SESSIONS", "1")
    with pytest.raises(SystemExit, match="name"):
        main(["test-session", "--user", "Bad Name"])
    assert hosted.users == {}


@pytest.mark.parametrize(("value", "logged"), [("1", True), ("", False)])
def test_start_up_says_when_the_switch_is_on(monkeypatch, caplog, value, logged):
    monkeypatch.setenv("KHADBOT_TEST_SESSIONS", value)
    with caplog.at_level(logging.WARNING):
        _log_development_switches()
    assert ("KHADBOT_TEST_SESSIONS" in caplog.text) is logged


def test_closes_its_pool_on_success(monkeypatch, hosted):
    monkeypatch.setenv("KHADBOT_TEST_SESSIONS", "1")
    main(["test-session", "--user", "alice", "--quiet"])
    assert hosted.pool_closed == [True]


def test_closes_its_pool_when_issuing_fails(monkeypatch, hosted):
    monkeypatch.setenv("KHADBOT_TEST_SESSIONS", "1")
    with pytest.raises(SystemExit):
        main(["test-session", "--user", "Bad Name"])
    assert hosted.pool_closed == [True]
