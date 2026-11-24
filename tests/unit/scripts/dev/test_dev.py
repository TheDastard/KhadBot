"""
tests/unit/scripts/dev/test_dev.py

scripts/dev/dev.py, the native dev loop runner: which environment the API and worker get, the command lines it builds,
the port it checks and how it renders and files the processes' log lines. It starts no process and touches no Docker.
"""

from __future__ import annotations

import io
import json
import sys

import pytest
from helpers.scripts import load_script

dev = load_script("dev", "dev")


@pytest.fixture
def clean_environment(monkeypatch):
    for name in (
        "DATABASE_URL",
        "KHADBOT_MODE",
        "KHADBOT_TOKEN_KEYS",
        "WARCRAFTLOGS_CLIENT_ID",
        "WARCRAFTLOGS_CLIENT_SECRET",
    ):
        monkeypatch.delenv(name, raising=False)


class TestEnvironment:
    def test_the_dev_file_wins_over_the_process_and_dotenv_environment(self, monkeypatch, tmp_path, clean_environment):
        dev_file = tmp_path / "dev.env"
        dev_file.write_text("DATABASE_URL=postgresql://dev\nKHADBOT_MODE=hosted\n")
        monkeypatch.setattr(dev, "DEV_ENV", dev_file)
        monkeypatch.setenv("DATABASE_URL", "postgresql://elsewhere")
        env = dev.dev_environment()
        assert env["DATABASE_URL"] == "postgresql://dev"
        assert env["KHADBOT_MODE"] == "hosted"

    def test_missing_credentials_get_placeholders(self, monkeypatch, tmp_path, clean_environment, capsys):
        dev_file = tmp_path / "dev.env"
        dev_file.write_text("KHADBOT_MODE=hosted\n")
        monkeypatch.setattr(dev, "DEV_ENV", dev_file)
        monkeypatch.setattr("dotenv.find_dotenv", lambda *a, **k: "")
        env = dev.dev_environment()
        assert env["WARCRAFTLOGS_CLIENT_ID"] == dev.PLACEHOLDER_WCL["WARCRAFTLOGS_CLIENT_ID"]
        assert "placeholders" in capsys.readouterr().out

    def test_real_credentials_are_kept(self, monkeypatch, tmp_path, clean_environment):
        dev_file = tmp_path / "dev.env"
        dev_file.write_text("KHADBOT_MODE=hosted\n")
        monkeypatch.setattr(dev, "DEV_ENV", dev_file)
        monkeypatch.setenv("WARCRAFTLOGS_CLIENT_ID", "id-from-env")
        monkeypatch.setenv("WARCRAFTLOGS_CLIENT_SECRET", "secret-from-env")
        env = dev.dev_environment()
        assert env["WARCRAFTLOGS_CLIENT_ID"] == "id-from-env"

    def test_a_missing_dev_file_names_the_example(self, monkeypatch, tmp_path):
        monkeypatch.setattr(dev, "DEV_ENV", tmp_path / "missing.env")
        with pytest.raises(SystemExit, match="dev.env.example"):
            dev.dev_environment()

    def test_the_example_is_a_hosted_local_stack_file(self):
        from dotenv import dotenv_values

        values = dotenv_values(dev.DEV_ENV_EXAMPLE)
        assert values["KHADBOT_MODE"] == "hosted"
        assert values["KHADBOT_STORAGE"] == "s3"
        assert "127.0.0.1:5432" in values["DATABASE_URL"]
        assert values["KHADBOT_S3_ENDPOINT"] == "http://127.0.0.1:8333"
        assert "WARCRAFTLOGS_CLIENT_SECRET" not in values  # real credentials never live in this file


class TestCommands:
    def test_the_api_runs_main_with_reload_on_loopback(self):
        command = dev.khadbot_command("api", [])
        assert command[0] == sys.executable
        assert command[2:] == ["api", "--reload", "--host", "127.0.0.1"]

    def test_the_worker_runs_main_with_reload(self):
        assert dev.khadbot_command("worker", [])[2:] == ["worker", "--reload"]

    def test_the_port_comes_from_the_arguments(self):
        assert dev.api_port([]) == 8000
        assert dev.api_port(["--port", "8123"]) == 8123
        assert dev.api_port(["--port=8124"]) == 8124
        assert dev.khadbot_command("api", ["--port", "8123"])[-2:] == ["--port", "8123"]

    def test_a_busy_port_is_refused(self, monkeypatch):
        monkeypatch.setattr(dev, "port_in_use", lambda port: True)
        with pytest.raises(SystemExit, match="stack:down"):
            dev.require_free_port(8000)


API_LINE = json.dumps(
    {
        "time": "2026-11-24T19:02:11.483+00:00",
        "level": "INFO",
        "logger": "khadbot.api.request_log",
        "message": "GET /v1/health 200",
        "service": "api",
        "env": "dev",
        "request_id": "7f3a9c0e5b2d4e61",
        "event": "http.request",
        "status": 200,
    }
)


class TestLogLines:
    def test_the_api_and_worker_log_json(self):
        env = dev.khadbot_environment({"KHADBOT_LOG_FORMAT": "text", "KHADBOT_MODE": "hosted"})
        assert env == {"KHADBOT_LOG_FORMAT": "json", "KHADBOT_MODE": "hosted"}

    def test_a_json_line_renders_compactly_without_the_process_fields(self):
        assert dev.render_line(API_LINE) == (
            "19:02:11 INFO khadbot.api.request_log: GET /v1/health 200 "
            "request_id=7f3a9c0e5b2d4e61 event=http.request status=200"
        )

    def test_an_exception_follows_on_its_own_lines(self):
        line = json.dumps({"level": "ERROR", "logger": "x", "message": "boom", "exc": "Traceback\n  ValueError"})
        assert dev.render_line(line).splitlines() == ["ERROR x: boom", "Traceback", "  ValueError"]

    def test_other_output_renders_as_it_is(self):
        assert dev.render_line("[reload] dev.py changed; restarting") == "[reload] dev.py changed; restarting"
        assert dev.render_line('{"not": "a log line"}') == '{"not": "a log line"}'

    def test_a_json_line_is_filed_as_it_is(self):
        assert dev.json_line(API_LINE, "api") == API_LINE

    def test_other_output_is_filed_as_a_redacted_json_line(self):
        entry = json.loads(dev.json_line("\x1b[32m ready\x1b[0m postgres://u:hunter2@db", "web"))
        assert entry["service"] == "web" and entry["level"] == "INFO" and entry["logger"] == "output"
        assert entry["message"] == " ready postgres://u:<redacted>@db"
        assert entry["time"]

    def test_pump_prefixes_the_terminal_and_files_every_line(self, tmp_path, capsys):
        log = dev.open_log("api", tmp_path)
        dev.pump(io.StringIO(f"{API_LINE}\nplain output\n\n"), "api", log)
        assert log.closed
        out = capsys.readouterr().out.splitlines()
        assert out == [
            "[api] 19:02:11 INFO khadbot.api.request_log: GET /v1/health 200 "
            "request_id=7f3a9c0e5b2d4e61 event=http.request status=200",
            "[api] plain output",
            "[api] ",
        ]
        filed = (tmp_path / "api.jsonl").read_text(encoding="utf-8").splitlines()
        assert filed[0] == API_LINE
        assert json.loads(filed[1])["message"] == "plain output"
        assert len(filed) == 2  # blank lines are not filed

    def test_each_start_empties_the_file(self, tmp_path):
        (tmp_path / "worker.jsonl").write_text("old\n", encoding="utf-8")
        dev.open_log("worker", tmp_path).close()
        assert (tmp_path / "worker.jsonl").read_text(encoding="utf-8") == ""

    def test_the_files_live_at_the_checkout_root(self):
        assert dev.DEV_LOGS == dev.REPO / ".khadbot_dev_logs"


class TestStopping:
    """Stopping the loop signals every process tree before waiting on any, and ignores repeat stop requests."""

    class FakeProc:
        def __init__(self, name: str, events: list[str]) -> None:
            self.name, self.events, self.pid = name, events, 0

        def poll(self):
            return None

        def wait(self, timeout=None):
            self.events.append(f"wait {self.name}")
            return 0

        def kill(self):
            self.events.append(f"kill {self.name}")

    def test_every_tree_is_signalled_before_any_is_waited_for(self, monkeypatch):
        import signal

        events: list[str] = []
        monkeypatch.setattr(dev, "signal_tree", lambda proc: events.append(f"signal {proc.name}"))
        procs = {name: self.FakeProc(name, events) for name in ("api", "worker", "web")}
        try:
            dev.stop_all(procs)
        finally:
            signal.signal(signal.SIGINT, signal.default_int_handler)
            signal.signal(getattr(signal, "SIGBREAK", signal.SIGTERM), signal.SIG_DFL)
        assert events == ["signal api", "signal worker", "signal web", "wait api", "wait worker", "wait web"]

    def test_stop_requests_are_ignored_while_stopping(self, monkeypatch):
        import signal

        seen: dict[str, object] = {}

        def record(proc):
            seen["term"] = signal.getsignal(getattr(signal, "SIGBREAK", signal.SIGTERM))
            seen["int"] = signal.getsignal(signal.SIGINT)

        monkeypatch.setattr(dev, "signal_tree", record)
        try:
            dev.stop_all({"web": self.FakeProc("web", [])})
        finally:
            signal.signal(signal.SIGINT, signal.default_int_handler)
            signal.signal(getattr(signal, "SIGBREAK", signal.SIGTERM), signal.SIG_DFL)
        assert seen == {"term": signal.SIG_IGN, "int": signal.SIG_IGN}
