"""
tests/unit/test_main.py

Argparse dispatch for khadbot.main. _run_cli is patched; no agent is built.
"""

import json
import logging
import os
import sys
from unittest.mock import patch

import pytest

from khadbot.logging_setup import JsonFormatter, TextFormatter
from khadbot.main import main


@pytest.fixture(autouse=True)
def default_log_settings(monkeypatch):
    """The logging defaults under test, whatever the shell sets (``conftest`` restores the root logger)."""
    monkeypatch.delenv("KHADBOT_LOG_FORMAT", raising=False)
    monkeypatch.delenv("KHADBOT_LOG_LEVEL", raising=False)


def khadbot_handler() -> logging.StreamHandler:
    """The one handler ``configure_logging`` installed on the root logger."""
    found = [h for h in logging.getLogger().handlers if getattr(h, "_khadbot_handler", False)]
    assert len(found) == 1
    assert isinstance(found[0], logging.StreamHandler)
    return found[0]


class TestDispatch:
    def test_no_args_defaults_to_cli(self):
        with patch("khadbot.main._run_cli") as run_cli:
            main([])
        run_cli.assert_called_once_with()

    def test_explicit_cli(self):
        with patch("khadbot.main._run_cli") as run_cli:
            main(["cli"])
        run_cli.assert_called_once_with()

    def test_unknown_command_exits(self):
        with patch("khadbot.main._run_cli") as run_cli, pytest.raises(SystemExit):
            main(["bogus"])
        run_cli.assert_not_called()


class TestIngestSubcommand:
    def test_parses_args(self):
        with patch("khadbot.main._run_ingest") as run:
            main(["ingest", "ABC123", "--fights", "3,4", "--offline"])
        run.assert_called_once_with("ABC123", "3,4", True, False)

    def test_defaults(self):
        with patch("khadbot.main._run_ingest") as run:
            main(["ingest", "ABC123"])
        run.assert_called_once_with("ABC123", None, False, False)

    def test_refresh_meta(self):
        with patch("khadbot.main._run_ingest") as run:
            main(["ingest", "ABC123", "--refresh-meta"])
        run.assert_called_once_with("ABC123", None, False, True)

    def test_refresh_meta_with_offline_is_a_usage_error(self, capsys):
        with patch("khadbot.main._run_ingest") as run, pytest.raises(SystemExit) as exc:
            main(["ingest", "ABC123", "--refresh-meta", "--offline"])
        assert exc.value.code == 2
        assert "--refresh-meta" in capsys.readouterr().err
        run.assert_not_called()

    def test_requires_code(self):
        with pytest.raises(SystemExit):
            main(["ingest"])


class TestDigestSubcommand:
    def test_parses_args(self):
        with patch("khadbot.main._run_digest") as run:
            main(["digest", "ABC123", "4", "--json"])
        run.assert_called_once_with("ABC123", 4, True, False)

    def test_unregistered(self):
        with patch("khadbot.main._run_digest") as run:
            main(["digest", "ABC123", "4", "--unregistered"])
        run.assert_called_once_with("ABC123", 4, False, True)

    def test_fight_must_be_int(self):
        with pytest.raises(SystemExit):
            main(["digest", "ABC123", "four"])


class TestServeSubcommand:
    def test_default_port(self):
        with patch("khadbot.main._run_serve") as run:
            main(["serve"])
        run.assert_called_once_with(8000)

    def test_port(self):
        with patch("khadbot.main._run_serve") as run:
            main(["serve", "--port", "8123"])
        run.assert_called_once_with(8123)

    def test_binds_to_loopback_only(self, tmp_path, monkeypatch):
        from khadbot.main import _run_serve

        monkeypatch.setenv("KHADBOT_DATA_DIR", str(tmp_path))
        with patch("uvicorn.run") as run:
            _run_serve(8123)
        app = run.call_args.args[0]
        assert run.call_args.kwargs == {
            "host": "127.0.0.1",
            "port": 8123,
            "log_config": None,
            "access_log": False,
        }  # via the root handler
        assert app.state.ctx.data_dir == tmp_path
        assert app.state.mode == "local"


class TestLocalLogging:
    """The commands run from a terminal log redacted text: ``serve`` at INFO, the rest to stderr at WARNING."""

    def test_serve_logs_text_at_info_through_the_root_handler(self, tmp_path, monkeypatch):
        monkeypatch.setenv("KHADBOT_DATA_DIR", str(tmp_path))
        with patch("uvicorn.run") as run:
            main(["serve", "--port", "8123"])
        handler = khadbot_handler()
        assert isinstance(handler.formatter, TextFormatter)
        assert handler.stream is sys.stdout
        assert logging.getLogger().level == logging.INFO
        assert run.call_args.kwargs["log_config"] is None

    def test_serve_keeps_an_explicit_json_format(self, tmp_path, monkeypatch):
        monkeypatch.setenv("KHADBOT_DATA_DIR", str(tmp_path))
        monkeypatch.setenv("KHADBOT_LOG_FORMAT", "json")
        with patch("uvicorn.run"):
            main(["serve"])
        assert isinstance(khadbot_handler().formatter, JsonFormatter)

    def test_offline_ingest_logs_warnings_as_text_on_stderr(self):
        with patch("khadbot.main._run_ingest") as run:
            main(["ingest", "ABC123", "--offline"])
        run.assert_called_once()
        handler = khadbot_handler()
        assert isinstance(handler.formatter, TextFormatter)
        assert handler.stream is sys.stderr
        assert logging.getLogger().level == logging.WARNING

    @pytest.mark.parametrize("argv", [["cli"], [], ["digest", "ABC123", "4"], ["test-session"]])
    def test_other_terminal_commands_log_to_stderr_at_warning(self, argv):
        with (
            patch("khadbot.main._run_cli"),
            patch("khadbot.main._run_digest"),
            patch("khadbot.main._run_test_session"),
        ):
            main(argv)
        assert khadbot_handler().stream is sys.stderr
        assert logging.getLogger().level == logging.WARNING

    def test_an_explicit_level_wins(self, monkeypatch):
        monkeypatch.setenv("KHADBOT_LOG_LEVEL", "debug")
        with patch("khadbot.main._run_ingest"):
            main(["ingest", "ABC123", "--offline"])
        assert logging.getLogger().level == logging.DEBUG

    def test_settings_in_dotenv_reach_the_logging(self, tmp_path, monkeypatch):
        env_file = tmp_path / ".env"
        env_file.write_text("KHADBOT_LOG_LEVEL=DEBUG\nKHADBOT_LOG_FORMAT=json\n", encoding="utf-8")
        monkeypatch.setattr("dotenv.find_dotenv", lambda *args, **kwargs: str(env_file))
        with patch("khadbot.main._run_ingest"):
            main(["ingest", "ABC123", "--offline"])
        assert logging.getLogger().level == logging.DEBUG
        assert isinstance(khadbot_handler().formatter, JsonFormatter)

    def test_hosted_commands_load_dotenv_too(self, tmp_path, monkeypatch):
        env_file = tmp_path / ".env"
        env_file.write_text("KHADBOT_TEST_DOTENV_MARKER=from-dotenv\n", encoding="utf-8")
        monkeypatch.setattr("dotenv.find_dotenv", lambda *args, **kwargs: str(env_file))
        monkeypatch.delenv("KHADBOT_TEST_DOTENV_MARKER", raising=False)
        try:
            with patch("khadbot.main._run_rotate_token_keys"):
                main(["rotate-token-keys"])
            assert os.environ.get("KHADBOT_TEST_DOTENV_MARKER") == "from-dotenv"
        finally:
            os.environ.pop("KHADBOT_TEST_DOTENV_MARKER", None)

    def test_importing_the_coach_leaves_the_environment_alone(self, monkeypatch):
        import importlib

        import dotenv

        monkeypatch.setattr(dotenv, "load_dotenv", lambda *a, **k: pytest.fail("the coach loaded .env"))
        import khadbot.agent.coach as coach

        importlib.reload(coach)

    def test_the_real_environment_wins_over_dotenv(self, tmp_path, monkeypatch):
        env_file = tmp_path / ".env"
        env_file.write_text("KHADBOT_LOG_LEVEL=DEBUG\n", encoding="utf-8")
        monkeypatch.setattr("dotenv.find_dotenv", lambda *args, **kwargs: str(env_file))
        monkeypatch.setenv("KHADBOT_LOG_LEVEL", "ERROR")
        with patch("khadbot.main._run_digest"):
            main(["digest", "ABC123", "4"])
        assert logging.getLogger().level == logging.ERROR

    def test_digest_json_keeps_stdout_clean_and_redacts_the_warning(self, capsys):
        def digest(*args):
            logging.getLogger("somelib").warning("retrying https://user:hunter2@example.com")
            logging.getLogger("somelib").info("not shown at WARNING")
            print(json.dumps({"pull": 4}))

        with patch("khadbot.main._run_digest", side_effect=digest):
            main(["digest", "ABC123", "4", "--json"])
        out, err = capsys.readouterr()
        assert json.loads(out) == {"pull": 4}
        assert "WARNING somelib: retrying https://user:<redacted>@example.com" in err
        assert "hunter2" not in err and "not shown" not in err


class TestApiSubcommand:
    def test_defaults_bind_every_interface_on_8000(self):
        with patch("khadbot.main._run_api") as run:
            main(["api"])
        run.assert_called_once_with("0.0.0.0", 8000)

    def test_host_and_port(self):
        with patch("khadbot.main._run_api") as run:
            main(["api", "--host", "127.0.0.1", "--port", "8123"])
        run.assert_called_once_with("127.0.0.1", 8123)

    def test_local_mode_serves_the_data_directory(self, tmp_path, monkeypatch):
        from khadbot.main import _run_api

        monkeypatch.setenv("KHADBOT_DATA_DIR", str(tmp_path))
        monkeypatch.delenv("KHADBOT_MODE", raising=False)
        with patch("uvicorn.run") as run:
            _run_api("0.0.0.0", 8123)
        assert run.call_args.kwargs == {
            "host": "0.0.0.0",
            "port": 8123,
            "log_config": None,
            "access_log": False,
        }  # uvicorn logs via the root handler; api/request_log.py writes the access line
        assert run.call_args.args[0].state.ctx.read_source is None
        assert run.call_args.args[0].state.mode == "local"  # no hosted settings: the read routes only

    def test_hosted_mode_without_a_database_url_exits_with_the_reason(self, monkeypatch):
        from khadbot.main import _run_api

        monkeypatch.setenv("KHADBOT_MODE", "hosted")
        monkeypatch.delenv("DATABASE_URL", raising=False)
        with patch("uvicorn.run") as run, pytest.raises(SystemExit) as exc:
            _run_api("0.0.0.0", 8123)
        assert "DATABASE_URL" in str(exc.value)
        run.assert_not_called()

    def test_hosted_mode_builds_the_app_labelled_hosted(self, monkeypatch):
        """With the hosted settings given, ``api`` passes ``mode="hosted"``; the services it builds are stubbed out."""
        from khadbot.main import _run_api

        monkeypatch.setenv("KHADBOT_MODE", "hosted")
        monkeypatch.setenv("DATABASE_URL", "postgresql://fake:fake@localhost/fake")
        with (
            patch("khadbot.db.get_pool"),
            patch("khadbot.jobs.queue.build_queue"),
            patch("khadbot.wiring.auth_service"),
            patch("khadbot.wiring.findings_store"),
            patch("khadbot.api.create_app", side_effect=ValueError("stop here")) as create,
            pytest.raises(SystemExit),
        ):
            _run_api("0.0.0.0", 8123)
        assert create.call_args.kwargs["mode"] == "hosted"
        assert create.call_args.kwargs["budget"] is not None  # access checks, lookups and searches reserve from it


class TestMigrateSubcommand:
    def test_dispatches(self):
        with patch("khadbot.main._run_migrate") as run:
            main(["migrate"])
        run.assert_called_once_with()

    def test_missing_database_url_exits_with_the_reason(self, monkeypatch):
        from khadbot.main import _run_migrate

        monkeypatch.delenv("DATABASE_URL", raising=False)
        with pytest.raises(SystemExit) as exc:
            _run_migrate()
        assert "DATABASE_URL" in str(exc.value)


class TestWorkerSubcommand:
    def test_parses_args(self):
        with patch("khadbot.main._run_worker") as run:
            main(["worker", "--queues", "ingest,process", "--concurrency", "3"])
        run.assert_called_once_with("ingest,process", 3)

    def test_defaults(self):
        with patch("khadbot.main._run_worker") as run:
            main(["worker"])
        run.assert_called_once_with(None, None)

    def test_refuses_to_run_in_local_mode(self, monkeypatch):
        monkeypatch.delenv("KHADBOT_MODE", raising=False)
        with pytest.raises(SystemExit, match="KHADBOT_MODE=hosted"):
            main(["worker"])


class TestBackfillSubcommand:
    def test_parses_args(self):
        with patch("khadbot.main._run_backfill") as run:
            main(["backfill", "--encounter", "3009", "--difficulty", "5", "--since", "2026-10-01", "--dry-run"])
        run.assert_called_once_with(3009, 5, "2026-10-01", True)

    def test_defaults(self):
        with patch("khadbot.main._run_backfill") as run:
            main(["backfill", "--encounter", "3009"])
        run.assert_called_once_with(3009, None, None, False)

    def test_the_encounter_is_required(self):
        with pytest.raises(SystemExit):
            main(["backfill"])

    def test_refuses_to_run_in_local_mode(self, monkeypatch):
        monkeypatch.delenv("KHADBOT_MODE", raising=False)
        with pytest.raises(SystemExit, match="KHADBOT_MODE=hosted"):
            main(["backfill", "--encounter", "3009"])


class TestWorkerCheck:
    def test_check_exits_zero_for_a_fresh_health_file(self, monkeypatch, tmp_path):
        from khadbot.jobs.health import beat

        path = tmp_path / "alive"
        monkeypatch.setenv("KHADBOT_WORKER_HEALTH_FILE", str(path))
        beat(path)
        with patch("khadbot.main._run_worker") as run:
            main(["worker", "--check"])  # returns without exiting
        run.assert_not_called()

    def test_check_fails_when_no_worker_wrote_the_file(self, monkeypatch, tmp_path):
        monkeypatch.setenv("KHADBOT_WORKER_HEALTH_FILE", str(tmp_path / "missing"))
        with pytest.raises(SystemExit, match="not alive"):
            main(["worker", "--check"])


class TestRotateTokenKeysSubcommand:
    def test_dispatches(self):
        with patch("khadbot.main._run_rotate_token_keys") as run:
            main(["rotate-token-keys"])
        run.assert_called_once_with()

    def test_refuses_to_run_in_local_mode(self, monkeypatch):
        monkeypatch.delenv("KHADBOT_MODE", raising=False)
        with pytest.raises(SystemExit, match="KHADBOT_MODE=hosted"):
            main(["rotate-token-keys"])

    def test_rotates_and_reports_the_count(self, monkeypatch, capsys):
        from unittest.mock import AsyncMock, MagicMock

        monkeypatch.setenv("KHADBOT_MODE", "hosted")
        monkeypatch.setenv("DATABASE_URL", "postgresql://fake:fake@localhost/fake")
        service = MagicMock()
        service.tokens.rotate_keys = AsyncMock(return_value=3)
        with patch("khadbot.wiring.auth_service", return_value=service), patch("khadbot.db.close_pool") as close:
            main(["rotate-token-keys"])
        service.tokens.rotate_keys.assert_awaited_once_with()
        close.assert_called_once_with()
        assert "3 user(s)" in capsys.readouterr().out


class TestUsageSubcommand:
    def test_parses_args(self):
        with patch("khadbot.main._run_usage") as run:
            main(["usage", "--since", "2026-10-01", "--by", "user"])
        run.assert_called_once_with("2026-10-01", "user")

    def test_groups_by_kind_by_default(self):
        with patch("khadbot.main._run_usage") as run:
            main(["usage", "--since", "2026-10-01"])
        run.assert_called_once_with("2026-10-01", "kind")

    def test_since_is_required_and_by_is_restricted(self):
        with pytest.raises(SystemExit):
            main(["usage"])
        with pytest.raises(SystemExit):
            main(["usage", "--since", "2026-10-01", "--by", "report"])

    def test_refuses_to_run_in_local_mode(self, monkeypatch):
        monkeypatch.delenv("KHADBOT_MODE", raising=False)
        with pytest.raises(SystemExit, match="KHADBOT_MODE=hosted"):
            main(["usage", "--since", "2026-10-01"])


class TestReload:
    def test_api_reload_runs_the_same_command_under_the_reloader(self):
        with patch("khadbot.main._run_reloading") as reloading, patch("khadbot.main._run_api") as run:
            main(["api", "--port", "9000", "--reload"])
        reloading.assert_called_once_with(["api", "--port", "9000"])
        run.assert_not_called()

    def test_worker_reload_keeps_its_other_arguments(self):
        with patch("khadbot.main._run_reloading") as reloading, patch("khadbot.main._run_worker") as run:
            main(["worker", "--queues", "ingest", "--reload"])
        reloading.assert_called_once_with(["worker", "--queues", "ingest"])
        run.assert_not_called()

    def test_without_reload_the_command_runs_directly(self):
        with patch("khadbot.main._run_reloading") as reloading, patch("khadbot.main._run_api") as run:
            main(["api", "--port", "9000"])
        reloading.assert_not_called()
        run.assert_called_once_with("0.0.0.0", 9000)

    def test_only_api_and_worker_take_reload(self):
        with pytest.raises(SystemExit):
            main(["migrate", "--reload"])

    def test_the_reloader_watches_the_package_and_config_and_restarts_the_child(self):
        from khadbot import main as main_module

        with patch("watchfiles.run_process") as run_process:
            main_module._run_reloading(["api"])
        (*paths,) = run_process.call_args.args
        assert main_module.PACKAGE_DIR in paths and main_module.CONFIG_DIR in paths
        assert run_process.call_args.kwargs["target"] is main_module._reload_child
        assert run_process.call_args.kwargs["args"] == (["api"],)

    def test_the_child_runs_the_command_without_reload(self):
        from khadbot import main as main_module

        with patch("khadbot.main._run_api") as run:
            main_module._reload_child(["api", "--port", "9000"])
        run.assert_called_once_with("0.0.0.0", 9000)

    def test_reload_without_the_dev_group(self, monkeypatch):
        import sys

        from khadbot import main as main_module

        monkeypatch.setitem(sys.modules, "watchfiles", None)
        with pytest.raises(SystemExit, match="'dev' dependency group"):
            main_module._run_reloading(["api"])


class TestWclReplayAnnouncement:
    """``api`` and ``worker`` say at start-up when WCL replay is on, and stop on replay settings that can't work."""

    @pytest.fixture(autouse=True)
    def _env(self, monkeypatch):
        from khadbot.config import reset_config

        monkeypatch.delenv("KHADBOT_WCL_REPLAY", raising=False)
        monkeypatch.delenv("KHADBOT_WCL_REPLAY_LIVE", raising=False)
        with patch("khadbot.logging_setup.configure_logging"):
            yield monkeypatch
        reset_config()

    def test_off_says_nothing(self, caplog):
        with patch("khadbot.main._run_worker") as run, caplog.at_level("WARNING"):
            main(["worker"])
        run.assert_called_once()
        assert "WCL replay" not in caplog.text

    def test_on_is_logged_before_the_worker_starts(self, _env, caplog, tmp_path):
        _env.setenv("KHADBOT_WCL_REPLAY", str(tmp_path))
        with patch("khadbot.main._run_api") as run, caplog.at_level("WARNING"):
            main(["api"])
        run.assert_called_once()
        assert f"WCL replay is on: WCL data requests are answered from {tmp_path}" in caplog.text
        assert "recorded fetch filters, per pull" in caplog.text

    def test_a_missing_directory_stops_the_process(self, _env, tmp_path):
        _env.setenv("KHADBOT_WCL_REPLAY", str(tmp_path / "missing"))
        with patch("khadbot.main._run_worker") as run, pytest.raises(SystemExit, match="not a directory"):
            main(["worker"])
        run.assert_not_called()
