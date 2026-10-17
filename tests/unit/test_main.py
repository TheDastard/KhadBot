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
