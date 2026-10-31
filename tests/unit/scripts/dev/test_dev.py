"""
tests/unit/scripts/dev/test_dev.py

scripts/dev/dev.py, the native dev loop runner: which environment the API and worker get, the command lines it builds
and the port it checks. It starts no process and touches no Docker.
"""

from __future__ import annotations

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
