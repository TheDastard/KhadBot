"""
tests/unit/scripts/dev/test_logs.py

scripts/dev/logs.py, the Loki query helper: the LogQL it builds, how it reads Loki's answers, what it prints and its
exit codes. Loki is a fake fetch function; no network.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse

import pytest
from helpers.scripts import load_script

logs = load_script("dev", "logs")

NOW = 1_800_000_000.0


def _query_of(url: str) -> dict[str, str]:
    return dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))


class FakeLoki:
    def __init__(self, result: list[dict]) -> None:
        self.result = result
        self.urls: list[str] = []

    def __call__(self, url: str) -> dict:
        self.urls.append(url)
        return {"status": "success", "data": {"result": self.result}}


def _run(argv: list[str], fetch) -> list[str]:
    return logs.run(logs.build_parser().parse_args(argv), fetch, NOW)


# ---------------------------------------------------------------------------
# Query building
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("text", "seconds"), [("30m", 1800), ("6h", 21600), ("7d", 604800)])
def test_parse_since(text, seconds):
    assert logs.parse_since(text) == seconds


@pytest.mark.parametrize("text", ["", "6", "1w", "-1h", "1.5h"])
def test_parse_since_refuses_other_forms(text):
    with pytest.raises(ValueError, match="--since"):
        logs.parse_since(text)


def test_summary_query_groups_problem_lines():
    query = logs.summary_query("stack", "WARNING", 86400)
    assert query == (
        'sum by (service, level, event, error_code, logger) (count_over_time({env="stack", '
        'level=~"WARNING|ERROR|CRITICAL"} | json logger="logger" [86400s]))'
    )


def test_summary_query_every_env_and_error_level():
    query = logs.summary_query(None, "error", 3600)
    assert '{env=~".+", level=~"ERROR|CRITICAL"}' in query


def test_unknown_level_is_refused():
    with pytest.raises(ValueError, match="--level"):
        logs.levels_from("INFO")


def test_lines_query_combines_filters():
    args = logs.build_parser().parse_args(
        [
            "lines",
            "--env",
            "dev",
            "--service",
            "worker",
            "--level",
            "ERROR",
            "--event",
            "job.finish",
            "--error-code",
            "pulls_failed",
            "--contains",
            "Job",
            "--logger",
            "khadbot.jobs.queue",
        ]
    )
    assert logs.lines_query(args) == (
        '{env="dev", service="worker", level=~"ERROR|CRITICAL"} | event="job.finish" | error_code="pulls_failed"'
        ' |= "Job" | json logger="logger" | logger="khadbot.jobs.queue"'
    )


def test_lines_query_quotes_values():
    args = logs.build_parser().parse_args(["lines", "--contains", 'say "hi"\\'])
    assert logs.lines_query(args).endswith(' |= "say \\"hi\\"\\\\"')


def test_lines_query_ignore_case_uses_a_case_insensitive_regex():
    args = logs.build_parser().parse_args(["lines", "--contains", "Job failed", "--ignore-case"])
    assert logs.lines_query(args).endswith(' |~ "(?i)Job failed"')


def test_lines_query_ignore_case_escapes_regex_characters_for_re2():
    args = logs.build_parser().parse_args(["lines", "--contains", 'a.b (x) [1] say "hi"\\', "--ignore-case"])
    query = logs.lines_query(args)
    assert query.endswith(r' |~ "(?i)a\\.b \\(x\\) \\[1\\] say \"hi\"\\\\"')
    pattern = json.loads(query.split(" |~ ", 1)[1])
    assert re.search(pattern, 'A.B (X) [1] SAY "HI"\\')
    assert not re.search(pattern, "aXb (x) [1] say")


def test_ignore_case_without_contains_is_refused(capsys):
    assert logs.main(["lines", "--ignore-case"], fetch=FakeLoki([])) == 1
    assert "--ignore-case applies to --contains" in capsys.readouterr().err


def test_follow_query_matches_any_id_by_prefix():
    query = logs.follow_query("7f3a9c0e", "stack")
    assert query == (
        '{env="stack"} | request_id=~"7f3a9c0e.*" or job_id=~"7f3a9c0e.*" or report_code=~"7f3a9c0e.*"'
        ' or user_id=~"7f3a9c0e.*"'
    )


def test_follow_query_escapes_regex_characters():
    assert "pull_key" not in logs.follow_query("H7z1:15", None)
    assert 'request_id=~"H7z1:15.*"' in logs.follow_query("H7z1:15", None)
    assert 'request_id=~"a\\\\-b.*"' in logs.follow_query("a-b", None)


@pytest.mark.parametrize("value", ["", 'x"} | y', "a b", "x" * 81])
def test_follow_refuses_values_that_are_not_ids(value):
    with pytest.raises(ValueError, match="ID"):
        logs.follow_query(value, None)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def test_summary_prints_groups_largest_first():
    fake = FakeLoki(
        [
            {
                "metric": {
                    "service": "api",
                    "level": "WARNING",
                    "event": "http.request",
                    "error_code": "rate_limited",
                    "logger": "khadbot.api.request_log",
                },
                "value": [NOW, "2"],
            },
            {
                "metric": {
                    "service": "worker",
                    "level": "ERROR",
                    "event": "job.finish",
                    "error_code": "pulls_failed",
                    "logger": "khadbot.jobs.queue",
                },
                "value": [NOW, "9"],
            },
            {"metric": {"service": "worker", "level": "WARNING"}, "value": [NOW, "0"]},
        ]
    )
    out = _run(["summary", "--since", "6h", "--env", "stack"], fake)
    table = out[0].splitlines()
    assert table[0].split() == ["count", "service", "level", "event", "error_code", "logger"]
    assert table[1].split()[:5] == ["9", "worker", "ERROR", "job.finish", "pulls_failed"]
    assert table[2].split()[:2] == ["2", "api"]
    assert len(table) == 3  # the zero-count group is left out
    sent = _query_of(fake.urls[0])
    assert fake.urls[0].startswith("http://127.0.0.1:3100/loki/api/v1/query?")
    assert sent["time"] == str(int(NOW))
    assert "[21600s]" in sent["query"]


def test_summary_json_and_empty_text():
    fake = FakeLoki([{"metric": {"service": "api", "level": "ERROR"}, "value": [NOW, "3"]}])
    row = json.loads(_run(["summary", "--json"], fake)[0])
    assert row == {"count": 3, "service": "api", "level": "ERROR", "event": "", "error_code": "", "logger": ""}
    assert _run(["summary"], FakeLoki([])) == ["No warnings or errors in the window."]


def _stream(labels: dict, *lines: tuple[int, object]) -> dict:
    return {
        "stream": labels,
        "values": [[str(ts), text if isinstance(text, str) else json.dumps(text)] for ts, text in lines],
    }


def test_follow_prints_lines_oldest_first_with_ids():
    fake = FakeLoki(
        [
            _stream(
                {"service": "worker", "level": "INFO", "env": "stack"},
                (
                    3,
                    {
                        "time": "t3",
                        "level": "INFO",
                        "message": "Job j1 started",
                        "event": "job.start",
                        "job_id": "j1",
                        "request_id": "r1",
                    },
                ),
            ),
            _stream(
                {"service": "api", "level": "INFO", "env": "stack"},
                (
                    1,
                    {
                        "time": "t1",
                        "level": "INFO",
                        "message": "POST /v1/reports 202",
                        "event": "http.request",
                        "request_id": "r1",
                        "status": 202,
                    },
                ),
            ),
        ]
    )
    out = _run(["follow", "r1", "--since", "1d"], fake)
    assert out[0].startswith("t1 INFO api [http.request] POST /v1/reports 202")
    assert "request_id=r1" in out[0] and "status=202" in out[0]
    assert out[1].startswith("t3 INFO worker [job.start] Job j1 started")
    sent = _query_of(fake.urls[0])
    assert fake.urls[0].startswith("http://127.0.0.1:3100/loki/api/v1/query_range?")
    assert sent["direction"] == "forward"
    assert sent["start"] == str(int((NOW - 86400) * 1e9))
    assert sent["limit"] == "200"


def test_lines_newest_first_plain_text_lines_and_labels():
    fake = FakeLoki(
        [_stream({"service": "postgres", "level": "", "env": "stack"}, (1, "FATAL: oops"), (2, "LOG: later"))]
    )
    out = _run(["lines", "--service", "postgres", "--limit", "5"], fake)
    assert out[0].endswith("LOG: later") and out[1].endswith("FATAL: oops")
    assert "postgres" in out[0]
    assert _query_of(fake.urls[0])["direction"] == "backward"


def test_lines_strip_terminal_escapes_and_control_characters():
    fake = FakeLoki([_stream({"service": "api"}, (1, {"message": "\x1b[31mred\x1b[0m\x07 text", "job_id": "a\x00b"}))])
    out = _run(["lines"], fake)[0]
    assert "\x1b" not in out and "\x07" not in out and "\x00" not in out
    assert "red text" in out and "job_id=ab" in out


def test_lines_show_the_last_line_of_an_exception():
    fake = FakeLoki(
        [_stream({"service": "worker"}, (1, {"message": "boom", "exc": "Traceback\n  ...\nValueError: x"}))]
    )
    assert _run(["lines"], fake)[0].endswith("\n    exc: ValueError: x")


def test_lines_json_keeps_the_record():
    fake = FakeLoki([_stream({"service": "api", "level": "INFO", "env": "dev"}, (1, {"message": "m", "status": 200}))])
    assert json.loads(_run(["lines", "--json"], fake)[0]) == {
        "message": "m",
        "status": 200,
        "service": "api",
        "level": "INFO",
        "env": "dev",
    }


def test_no_lines_message():
    assert _run(["lines"], FakeLoki([])) == ["No matching lines in the window."]


def test_loki_url_option():
    fake = FakeLoki([])
    _run(["summary", "--loki", "http://loki.test:3100/"], fake)
    assert fake.urls[0].startswith("http://loki.test:3100/loki/api/v1/query?")


# ---------------------------------------------------------------------------
# Exit codes
# ---------------------------------------------------------------------------


def test_main_prints_and_exits_zero(capsys):
    assert logs.main(["summary"], fetch=FakeLoki([])) == 0
    assert "No warnings or errors" in capsys.readouterr().out


def test_main_exits_two_when_loki_is_down(capsys):
    def down(url: str) -> dict:
        raise urllib.error.URLError("refused")

    assert logs.main(["summary"], fetch=down) == 2
    assert "mise run obs:up" in capsys.readouterr().err


def test_main_exits_one_on_bad_input(capsys):
    assert logs.main(["follow", "not an id"], fetch=FakeLoki([])) == 1
    assert "ID" in capsys.readouterr().err
