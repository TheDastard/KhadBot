"""
tests/unit/test_main.py

Argparse dispatch for khadbot.main. _run_cli is patched; no agent is built.
"""

from unittest.mock import patch

import pytest

from khadbot.main import main


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
        assert run.call_args.kwargs == {"host": "127.0.0.1", "port": 8123}
        assert app.state.ctx.data_dir == tmp_path
        assert app.state.mode == "local"


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
        }  # uvicorn logs via the root handler
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
