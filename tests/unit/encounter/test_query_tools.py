"""
tests/unit/encounter/test_query_tools.py

Analyst query tools on SYNTH01: the capped envelope (rows and characters, truncated/total/note),
scope enforcement, each tool's filters and failure paths, the 30 s event window limit, and refs on
every row that resolve to records consistent with the row.
"""

from __future__ import annotations

import json

import pytest

from khadbot.encounter import query_tools as qt
from khadbot.encounter.models import ToolResult
from khadbot.encounter.query_tools import AnalystScope, envelope


@pytest.fixture
def scope(synth_data) -> AnalystScope:
    return AnalystScope(synth_data, ("SYNTH01:1", "SYNTH01:2"))


def assert_rows_match_records(result: ToolResult, data) -> None:
    """Every row carries a ref, and each field it shares with the resolved record has the same value."""
    rows = result.data if isinstance(result.data, list) else result.data.get("clusters", [])
    for row in rows:
        assert row["ref"] in result.refs
        record = data.resolve(row["ref"])
        if "cluster" in row:  # cluster rows resolve through their mechanic
            record = next(c for c in record["cluster_rows"] if c["cluster"] == row["cluster"])
        shared = {k for k in row if k in record and k != "ref"}
        assert shared, row
        assert {k: row[k] for k in shared} == {k: record[k] for k in shared}


# ===========================================================================
# Envelope
# ===========================================================================


def test_envelope_caps_rows():
    rows = [{"ref": f"ev:R:1:death:{i}", "i": i} for i in range(1, 31)]
    result = envelope(rows, hint="Narrow it.")
    assert (len(result.data), result.total, result.truncated) == (25, 30, True)
    assert result.refs == [r["ref"] for r in rows[:25]]
    assert result.note == "Showing 25 of 30 rows. Narrow it."


def test_envelope_caps_characters():
    rows = [{"ref": f"ev:R:1:death:{i}", "blob": "x" * 500} for i in range(1, 11)]
    result = envelope(rows)
    assert len(result.model_dump_json()) <= qt.CHAR_CAP
    assert result.truncated and result.total == 10 and 1 <= len(result.data) < 10
    assert result.note and result.note.startswith(f"Showing {len(result.data)} of 10 rows.")


def test_envelope_keeps_one_oversized_row():
    result = envelope([{"ref": "ev:R:1:death:1", "blob": "x" * 5_000}, {"ref": "ev:R:1:death:2"}])
    assert len(result.data) == 1 and result.truncated


def test_envelope_untruncated_has_no_note():
    result = envelope([{"ref": "ev:R:1:pull"}])
    assert (result.ok, result.truncated, result.total, result.note) == (True, False, 1, None)


def test_envelope_wrap_and_head_refs():
    result = envelope(
        [{"ref": "ev:R:1:mech:a", "cluster": 1}],
        wrap=lambda kept: {"mechanic": {"ref": "ev:R:1:mech:a"}, "clusters": kept},
        head_refs=["ev:R:1:mech:a"],
    )
    assert result.refs == ["ev:R:1:mech:a"]
    assert result.data == {"mechanic": {"ref": "ev:R:1:mech:a"}, "clusters": [{"ref": "ev:R:1:mech:a", "cluster": 1}]}


# ===========================================================================
# Scope
# ===========================================================================


def test_out_of_scope_pull_is_refused(synth_data):
    narrow = AnalystScope(synth_data, ("SYNTH01:1",))
    result = qt.query_deaths_result(narrow, "SYNTH01:2")
    assert not result.ok and "not in this request's scope" in (result.note or "")
    assert result.data == [] and result.refs == []


def test_bare_fight_id_resolves_when_unambiguous(scope, synth_data):
    assert scope.pull_key("2") == "SYNTH01:2"
    assert qt.get_digest_result(scope, "2").ok
    two_reports = AnalystScope(synth_data, ("A:2", "B:2"))
    assert two_reports.pull_key("2") is None


def test_in_scope_but_not_ingested(synth_data):
    scope = AnalystScope(synth_data, ("SYNTH01:1", "SYNTH01:9"))
    result = qt.get_digest_result(scope, "SYNTH01:9")
    assert not result.ok and "not been ingested" in (result.note or "")
    listed = qt.list_pulls_result(scope)
    assert [r["pull_key"] for r in listed.data] == ["SYNTH01:1"]
    assert listed.note == "Not ingested: SYNTH01:9."


# ===========================================================================
# Tools
# ===========================================================================


def test_list_pulls(scope, synth_data):
    result = qt.list_pulls_result(scope)
    assert [(r["pull_key"], r["outcome"], r["deaths"]) for r in result.data] == [
        ("SYNTH01:1", "wipe", 4),
        ("SYNTH01:2", "kill", 1),
    ]
    assert_rows_match_records(result, synth_data)


def test_get_digest(scope):
    result = qt.get_digest_result(scope, "SYNTH01:1")
    assert result.ok and result.data["text"].startswith("PULL SYNTH01:1")
    assert result.refs[0] == "ev:SYNTH01:1:pull"
    assert "ev:SYNTH01:1:death:4" in result.refs and "ev:SYNTH01:1:win:cataclysm:2" in result.refs
    assert all(f"[{ref}]" in result.data["text"] for ref in result.refs[1:])


def test_query_deaths_hides_after_cutoff_by_default(scope, synth_data):
    result = qt.query_deaths_result(scope, "SYNTH01:1")
    assert [r["name"] for r in result.data] == ["Alphadps", "Gammadps", "Betadps"]
    assert "1 death(s) after the wipe cutoff hidden" in (result.note or "")
    everything = qt.query_deaths_result(scope, "SYNTH01:1", include_after_cutoff=True)
    assert [r["name"] for r in everything.data][-1] == "Tankyboi" and everything.note is None
    assert_rows_match_records(everything, synth_data)


def test_query_deaths_filters(scope):
    by_actor = qt.query_deaths_result(scope, "SYNTH01:1", actor="betadps")
    assert [r["ref"] for r in by_actor.data] == ["ev:SYNTH01:1:death:3"]
    by_phase = qt.query_deaths_result(scope, "SYNTH01:1", phase="p2", include_after_cutoff=True)
    assert [r["name"] for r in by_phase.data] == ["Betadps", "Tankyboi"]


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [({"actor": "Nobody"}, "No actor named 'Nobody'"), ({"phase": "p9"}, "Unknown phase 'p9'")],
)
def test_query_deaths_bad_filters(scope, kwargs, message):
    result = qt.query_deaths_result(scope, "SYNTH01:1", **kwargs)
    assert not result.ok and message in (result.note or "")


def test_query_mechanic(scope, synth_data):
    result = qt.query_mechanic_result(scope, "SYNTH01:2", "swirl")
    assert result.data["mechanic"]["failures"] == 14
    assert "cluster_rows" not in result.data["mechanic"]
    assert len(result.data["clusters"]) == 14 and result.refs == ["ev:SYNTH01:2:mech:swirl"]
    assert_rows_match_records(result, synth_data)


def test_query_mechanic_unknown(scope):
    result = qt.query_mechanic_result(scope, "SYNTH01:1", "nope")
    assert not result.ok and "Mechanics: swirl, shadow_bolt, curse" in (result.note or "")


def test_query_events_window_limit(scope):
    too_long = qt.query_events_result(scope, "SYNTH01:1", 0, 30_001)
    assert not too_long.ok and "maximum is 30,000 ms" in (too_long.note or "")
    backwards = qt.query_events_result(scope, "SYNTH01:1", 5_000, 1_000)
    assert not backwards.ok


def test_query_events_truncates_and_rows_resolve(scope, synth_data):
    result = qt.query_events_result(scope, "SYNTH01:2", 0, 30_000)
    assert result.ok and result.truncated and result.total and result.total > len(result.data)
    assert "Narrow t_from_ms/t_to_ms" in (result.note or "")
    assert len(result.model_dump_json()) <= qt.CHAR_CAP
    assert [r["seq"] for r in result.data] == sorted(r["seq"] for r in result.data)
    for row in result.data:
        assert synth_data.resolve(row["ref"]) == row


def test_query_events_filters(scope):
    deaths = qt.query_events_result(scope, "SYNTH01:1", 0, 30_000, types=["death"])
    assert [(r["t_ms"], r["target"]) for r in deaths.data] == [(8000, "Alphadps"), (23000, "Gammadps")]
    swirl = qt.query_events_result(scope, "SYNTH01:1", 0, 12_000, actor="Alphadps", ability_id=900101)
    assert [(r["t_ms"], r["type"]) for r in swirl.data] == [(5000, "damage"), (8000, "death"), (8000, "damage")]
    swirls = qt.query_events_result(
        scope, "SYNTH01:1", 0, 12_000, actor="Alphadps", ability_id=900101, types=["damage"]
    )
    assert [r["t_ms"] for r in swirls.data] == [5000, 8000]
    assert all(r["mechanic_id"] == "swirl" and r["failure"] for r in swirls.data)


def test_query_events_unknown_type_lists_present_types(scope):
    result = qt.query_events_result(scope, "SYNTH01:1", 0, 10_000, types=["explosion"])
    assert result.ok and result.data == []
    assert "Types present:" in (result.note or "") and "damage" in (result.note or "")


def test_query_events_unknown_actor(scope):
    assert not qt.query_events_result(scope, "SYNTH01:1", 0, 10_000, actor="Nobody").ok


def test_cooldown_timeline(scope, synth_data):
    result = qt.cooldown_timeline_result(scope, "SYNTH01:1")
    assert [r["ref"] for r in result.data] == [
        "ev:SYNTH01:1:cd:900301:1",
        "ev:SYNTH01:1:win:cataclysm:1",
        "ev:SYNTH01:1:cd:900302:1",
        "ev:SYNTH01:1:win:cataclysm:2",
    ]
    assert_rows_match_records(result, synth_data)
    p2 = qt.cooldown_timeline_result(scope, "SYNTH01:1", phase="p2")
    assert [r["ref"] for r in p2.data] == ["ev:SYNTH01:1:cd:900302:1", "ev:SYNTH01:1:win:cataclysm:2"]
    assert not qt.cooldown_timeline_result(scope, "SYNTH01:1", phase="p7").ok


@pytest.mark.parametrize(
    ("metric", "field", "values"),
    [
        ("deaths", "deaths", [4, 1]),
        ("phase_reached", "phase_reached", ["p2", "p2"]),
        ("duration", "duration_ms", [60_000, 80_000]),
    ],
)
def test_compare_pulls(scope, synth_data, metric, field, values):
    result = qt.compare_pulls_result(scope, metric)
    assert [r[field] for r in result.data] == values
    assert_rows_match_records(result, synth_data)


def test_compare_mechanic_failures(scope, synth_data):
    assert not qt.compare_pulls_result(scope, "mechanic_failures").ok
    result = qt.compare_pulls_result(scope, "mechanic_failures", "swirl")
    assert [(r["ref"], r["failures"]) for r in result.data] == [
        ("ev:SYNTH01:1:mech:swirl", 8),
        ("ev:SYNTH01:2:mech:swirl", 14),
    ]
    assert_rows_match_records(result, synth_data)
    unknown = qt.compare_pulls_result(scope, "mechanic_failures", "nope")
    assert [r["failures"] for r in unknown.data] == [None, None]


# ===========================================================================
# LangChain wrappers
# ===========================================================================


def test_build_analyst_tools():
    names = [t.name for t in qt.build_analyst_tools()]
    assert names == [
        "list_pulls",
        "get_digest",
        "query_deaths",
        "query_mechanic",
        "query_events",
        "cooldown_timeline",
        "compare_pulls",
        "submit_report",
    ]


def test_tool_schemas_hide_config():
    for t in qt.build_analyst_tools():
        assert "config" not in t.tool_call_schema.model_json_schema().get("properties", {})


async def test_tools_read_scope_from_run_config(scope):
    call = {"name": "query_deaths", "args": {"pull": "SYNTH01:1"}, "id": "c1", "type": "tool_call"}
    message = await qt.query_deaths.ainvoke(call, {"configurable": {"scope": scope}})
    result = ToolResult.model_validate(json.loads(message.content))
    assert result.ok and len(result.data) == 3

    for t in qt.build_analyst_tools()[:-1]:
        args = {
            "list_pulls": {},
            "get_digest": {"pull": "SYNTH01:2"},
            "query_deaths": {"pull": "SYNTH01:2"},
            "query_mechanic": {"pull": "SYNTH01:2", "mechanic_id": "swirl"},
            "query_events": {"pull": "SYNTH01:2", "t_from_ms": 0, "t_to_ms": 1000},
            "cooldown_timeline": {"pull": "SYNTH01:2"},
            "compare_pulls": {"metric": "deaths"},
        }[t.name]
        out = await t.ainvoke(args, {"configurable": {"scope": scope}})
        assert ToolResult.model_validate_json(out).ok, t.name


async def test_tool_without_scope_raises():
    with pytest.raises(RuntimeError, match="AnalystScope"):
        await qt.list_pulls.ainvoke({}, {"configurable": {}})


def test_submit_report_schema_requires_refs():
    schema = qt.SubmitReportArgs.model_json_schema()
    assert schema["$defs"]["SubmittedFinding"]["properties"]["refs"]["minItems"] == 1
    with pytest.raises(ValueError):
        qt.SubmitReportArgs.model_validate({"findings": [{"claim": "x", "refs": [], "confidence": "low"}]})
