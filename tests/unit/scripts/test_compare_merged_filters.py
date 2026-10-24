"""
tests/unit/scripts/test_compare_merged_filters.py

scripts/compare_merged_filters.py: filter merging, variant building, paging, row assignment, frame comparison and
the whole comparison against a stub WCL client that serves the synthetic SYNTH01 pages. No network.
"""

from __future__ import annotations

import shutil

import orjson
import pytest
from helpers.encounter.synth import (
    FIXTURES,
    SYNTH_DEFENSIVE_COOLDOWN_IDS,
    SYNTH_ENEMY_CAST_IDS,
    SYNTH_OFFENSIVE_COOLDOWN_IDS,
)
from helpers.scripts import load_script

from khadbot.encounter.ingest.fetch import RawPageCache, specs_for
from khadbot.encounter.ingest.normalize import normalize_pull, parse_report_meta
from khadbot.wcl.client import WarcraftLogsAPIError, WarcraftLogsRateLimitError

cmf = load_script("compare_merged_filters")

SYNTH_ROOT = FIXTURES / "wcl"


def synth_inputs():
    meta = RawPageCache(SYNTH_ROOT).report_meta("SYNTH01")
    pulls, actors = parse_report_meta("SYNTH01", meta)
    pull = next(p for p in pulls if p.fight_id == 1)
    specs = specs_for(SYNTH_DEFENSIVE_COOLDOWN_IDS, SYNTH_ENEMY_CAST_IDS[90001], SYNTH_OFFENSIVE_COOLDOWN_IDS)
    return pull, actors, specs


async def _no_sleep(_):
    return None


# ---------------------------------------------------------------------------
# Stub client
# ---------------------------------------------------------------------------


HEAL_FILTER = "HEAL-ONLY"  # the stub's stand-in for a heal-only filter expression


class StubWcl:
    """
    Serves SYNTH01 pages for the requested ``fightIDs`` (both fights share one encounter, so one spec list). A
    request with a dataType gets the matching spec's pages (or, for a merged filter, the union of the specs that
    filter covers); a request without one gets every event of that hostility once. A Healing request filtered with
    ``heal_filter`` gets the Healing rows whose type is in ``heal_types`` (None: every row, a filter that keeps too
    much); a filter in ``reject_filters`` raises that message. Every events request costs 2 points;
    ``rate_limit`` reads the counter. Each row's ``fight`` is set to the fight it was served from.
    """

    def __init__(self, cache: RawPageCache, specs, *, reject_no_type: str | None = None) -> None:
        self.cache = cache
        self.specs = specs
        self.reject_no_type = reject_no_type
        self.heal_filter = HEAL_FILTER
        self.heal_types: tuple[str, ...] | None = ("heal",)
        self.reject_filters: dict[str, str] = {}
        self.points = 100.0
        self.calls: list[dict] = []

    def _events(self, specs, fights=(1,)) -> list[dict]:
        seen: set[bytes] = set()
        out = []
        for fight in fights:
            for spec in specs:
                for page in self.cache.pages("SYNTH01", fight, spec):
                    for raw in page["data"]:
                        e = {**raw, "fight": fight}  # SYNTH01 rows say fight 0; WCL names the real fight
                        rid = cmf._row_id(e)
                        if rid not in seen:
                            seen.add(rid)
                            out.append(e)
        return sorted(out, key=lambda e: e["timestamp"])

    async def query(self, gql, variables=None, **_):
        assert variables is not None
        self.calls.append(variables)
        self.points += 2
        dt, host, flt = variables["dataType"], variables["hostility"], variables["filter"]
        fights = variables["fightIDs"]
        if flt in self.reject_filters:
            raise WarcraftLogsAPIError(self.reject_filters[flt])
        if dt is None:
            if self.reject_no_type:
                raise WarcraftLogsAPIError(self.reject_no_type)
            events = self._events([s for s in self.specs if s.hostility == host], fights)
        elif dt == "Healing" and flt == self.heal_filter:
            healing = [s for s in self.specs if s.data_type == "Healing"]
            events = [
                e for e in self._events(healing, fights) if self.heal_types is None or e["type"] in self.heal_types
            ]
        else:
            group = [s for s in self.specs if s.data_type == dt and s.hostility == host]
            exact = [s for s in group if s.filter_expression == flt]
            if exact:
                group = exact
            else:
                assert flt == cmf.merge_filters([s.filter_expression for s in group])
            events = self._events(group, fights)
        events = [e for e in events if variables["startTime"] <= e["timestamp"] <= variables["endTime"]]
        return {"reportData": {"report": {"events": {"data": events, "nextPageTimestamp": None}}}}

    async def rate_limit(self):
        return {"limitPerHour": 3600, "pointsSpentThisHour": self.points, "pointsResetIn": 100}


@pytest.fixture
def synth_stub(tmp_path):
    shutil.copytree(SYNTH_ROOT / "SYNTH01", tmp_path / "SYNTH01")
    pull, actors, specs = synth_inputs()
    return StubWcl(RawPageCache(tmp_path), specs), pull, actors, specs


# ---------------------------------------------------------------------------
# Filters and variants
# ---------------------------------------------------------------------------


def test_merge_filters_unions_ids():
    assert cmf.merge_filters(["ability.id IN (5,1)", "ability.id IN (3,1)"]) == "ability.id IN (1,3,5)"


def test_merge_filters_unfiltered_absorbs():
    assert cmf.merge_filters(["ability.id IN (1)", None]) is None
    assert cmf.merge_filters([]) is None


def test_merge_filters_refuses_unknown_syntax():
    with pytest.raises(ValueError, match="cannot merge"):
        cmf.merge_filters(["something else", "ability.id IN (1)"])


def test_build_variants_streams():
    _, _, specs = synth_inputs()
    variants = cmf.build_variants(specs)
    host = variants["hostility"]
    assert host.uncertain
    assert [(s.data_type, s.hostility) for s in host.streams] == [(None, "Friendlies"), (None, "Enemies")]
    assert host.streams[0].filter_expression is None
    assert host.streams[1].filter_expression == next(s.filter_expression for s in specs if s.hostility == "Enemies")
    typed = variants["datatype"]
    assert not typed.uncertain
    assert len(typed.streams) == len(specs) - 1  # the two Buffs requests become one
    buffs = next(s for s in typed.streams if s.data_type == "Buffs")
    assert buffs.filter_expression == cmf.merge_filters([s.filter_expression for s in specs if s.data_type == "Buffs"])


def test_build_variants_custom_filters():
    _, _, specs = synth_inputs()
    host = cmf.build_variants(specs, friendly_filter="F", enemy_filter="X")["hostility"]
    assert [s.filter_expression for s in host.streams] == ["F", "X"]


# ---------------------------------------------------------------------------
# Paging and probing
# ---------------------------------------------------------------------------


class ChainClient:
    def __init__(self, pages, errors=()):
        self.pages = list(pages)
        self.errors = list(errors)
        self.calls = []

    async def query(self, gql, variables=None, **_):
        self.calls.append(variables)
        if self.errors:
            raise self.errors.pop(0)
        page = self.pages[min(len(self.calls) - 1, len(self.pages) - 1)]
        return {"reportData": {"report": {"events": page}}}


STREAM = cmf.Stream("x", None, "Friendlies", None)


async def test_fetch_stream_follows_pages_to_the_end():
    pull, _, _ = synth_inputs()
    client = ChainClient(
        [{"data": [1], "nextPageTimestamp": pull.start_ms + 10}, {"data": [2], "nextPageTimestamp": None}]
    )
    result = await cmf.fetch_stream(client, "SYNTH01", pull, STREAM, page_limit=7, max_pages=5, sleep=_no_sleep)
    assert len(result.pages) == 2 and not result.aborted and not result.truncated
    assert [c["startTime"] for c in client.calls] == [pull.start_ms, pull.start_ms + 10]
    assert client.calls[0]["dataType"] is None and client.calls[0]["limit"] == 7


async def test_fetch_stream_page_cap_aborts():
    pull, _, _ = synth_inputs()
    pages = [{"data": [], "nextPageTimestamp": pull.start_ms + i} for i in range(1, 50)]
    client = ChainClient(pages)
    result = await cmf.fetch_stream(client, "SYNTH01", pull, STREAM, page_limit=1, max_pages=3, sleep=_no_sleep)
    assert result.aborted and len(result.pages) == 3


async def test_fetch_stream_stops_when_timestamp_does_not_advance():
    pull, _, _ = synth_inputs()
    client = ChainClient([{"data": [], "nextPageTimestamp": pull.start_ms}])
    result = await cmf.fetch_stream(client, "SYNTH01", pull, STREAM, page_limit=1, max_pages=9, sleep=_no_sleep)
    assert result.truncated and len(result.pages) == 1


async def test_fetch_stream_retries_rate_limit_then_gives_up():
    pull, _, _ = synth_inputs()
    ok = {"data": [], "nextPageTimestamp": None}
    client = ChainClient([ok], errors=[WarcraftLogsRateLimitError("slow", retry_after_s=0)])
    result = await cmf.fetch_stream(client, "SYNTH01", pull, STREAM, page_limit=1, max_pages=2, sleep=_no_sleep)
    assert len(result.pages) == 1 and len(client.calls) == 2
    client = ChainClient([ok], errors=[WarcraftLogsRateLimitError("slow")] * 9)
    with pytest.raises(WarcraftLogsRateLimitError):
        await cmf.fetch_stream(client, "SYNTH01", pull, STREAM, page_limit=1, max_pages=2, sleep=_no_sleep)


async def test_probe_reports_rejection_and_acceptance():
    pull, _, _ = synth_inputs()
    rejecting = ChainClient([], errors=[WarcraftLogsAPIError("Argument dataType is required")])
    message = await cmf.probe_stream(rejecting, "SYNTH01", pull, STREAM, sleep=_no_sleep)
    assert message == "Argument dataType is required"
    variables = rejecting.calls[0]
    assert variables["endTime"] == pull.start_ms + 1000 and variables["limit"] == 5
    accepting = ChainClient([{"data": [], "nextPageTimestamp": None}])
    assert await cmf.probe_stream(accepting, "SYNTH01", pull, STREAM, sleep=_no_sleep) is None


# ---------------------------------------------------------------------------
# Assignment and frame comparison
# ---------------------------------------------------------------------------


def _page(*events):
    return {"data": list(events), "nextPageTimestamp": None}


def test_assign_events_splits_extra_missing_and_ambiguous():
    a, b, c, d = ({"timestamp": t, "type": "cast", "sourceID": t} for t in (1, 2, 3, 4))
    baseline = {"Casts": [_page(a, b)], "Threat": [_page(b, c)]}
    result = cmf.assign_events([(None, [_page(a, b, d)])], baseline)  # c is missing; d is extra; b is in both specs
    assert result.extra == 1 and result.missing == 1 and result.ambiguous == 1
    assert result.pages_by_key["Casts"][0]["data"] == [a, b]
    assert result.pages_by_key["Threat"][0]["data"] == [b]


def test_assign_events_covers_limit_owners_per_stream():
    x = {"timestamp": 1, "type": "cast"}
    baseline = {"Casts": [_page(x)], "Threat": [_page(x)]}
    # two streams each return x; each only stands in for one spec
    result = cmf.assign_events([(("Casts",), [_page(x)]), (("Threat",), [_page(x)])], baseline)
    assert result.extra == 0 and result.missing == 0 and result.ambiguous == 0
    assert len(result.pages_by_key["Casts"][0]["data"]) == 1 and len(result.pages_by_key["Threat"][0]["data"]) == 1


def test_assign_events_keeps_duplicates_as_duplicates():
    a = {"timestamp": 1, "type": "cast"}
    result = cmf.assign_events([(None, [_page(a, a, a)])], {"Casts": [_page(a, a)]})
    assert len(result.pages_by_key["Casts"][0]["data"]) == 2 and result.extra == 1


def _synth_pages():
    pull, actors, specs = synth_inputs()
    cache = RawPageCache(SYNTH_ROOT)
    return pull, actors, {s.key: cache.pages("SYNTH01", pull.fight_id, s) for s in specs}


def test_compare_frames_identical_and_different():
    pull, actors, pages = _synth_pages()
    base = normalize_pull(pull, actors, pages)
    same = cmf.compare_frames(base, normalize_pull(pull, actors, pages))
    assert same.identical and same.identical_ignoring_tie_order and same.note == ""
    fewer = {k: v for k, v in pages.items() if k != "Healing"}
    different = cmf.compare_frames(base, normalize_pull(pull, actors, fewer))
    assert not different.identical and not different.identical_ignoring_tie_order
    assert different.rows_variant < different.rows_baseline and "rows" in different.note


def test_compare_frames_tie_order_only():
    pull, actors, pages = _synth_pages()
    base = normalize_pull(pull, actors, pages)
    reordered = base.reverse().with_columns(base["seq"])  # same rows in another order, same seq column
    result = cmf.compare_frames(base, reordered)
    assert result.identical_ignoring_tie_order and not result.identical
    assert "order differs" in result.note


# ---------------------------------------------------------------------------
# Savings and estimates
# ---------------------------------------------------------------------------


def test_savings_and_threshold():
    assert cmf.saving_pct(100, 80) == pytest.approx(20)
    assert cmf.saving_pct(0, 5) is None and cmf.saving_pct(None, 5) is None
    assert cmf.meets_threshold(20, None) and cmf.meets_threshold(None, 15.0)
    assert not cmf.meets_threshold(14.9, -3) and not cmf.meets_threshold(None, None)


def test_stream_cost_ceiling_uses_documented_model():
    assert cmf.stream_cost_ceiling(2, 60) == 2 * (2 + 59)


def test_estimates_state_their_sources():
    _, _, specs = synth_inputs()
    text = "\n".join(cmf.baseline_estimate(len(specs)))
    assert "docs/wcl-api.md" in text and "14 points" in text
    text = "\n".join(cmf.variant_estimate(cmf.build_variants(specs)["hostility"], 60))
    assert "probe" in text and "Ceiling" in text and "unknown" in text


# ---------------------------------------------------------------------------
# Whole comparison
# ---------------------------------------------------------------------------


async def test_run_comparison_variants_match_baseline(synth_stub):
    stub, pull, actors, specs = synth_stub
    asked: list[str] = []
    variants = list(cmf.build_variants(specs).values())
    result = await cmf.run_comparison(
        stub, "SYNTH01", pull, actors, specs, variants,
        confirm=lambda q: asked.append(q) or True, out=lambda s: None, sleep=_no_sleep,
    )  # fmt: skip
    assert len(asked) == 1 + len(variants)  # before the baseline and before each variant
    assert result.baseline is not None and result.baseline.requests == len(specs)
    assert result.baseline.points == 2 * len(specs)  # the stub charges 2 per request
    assert result.read_overhead == 0
    by_name = {v.name: v for v in result.variants}
    assert set(by_name) == {"hostility", "datatype"}
    for v in by_name.values():
        assert v.status == "ok"
        assert v.comparison is not None and v.comparison.identical_ignoring_tie_order
        assert v.extra_rows == 0 and v.missing_rows == 0
    assert by_name["datatype"].metrics.requests == len(specs) - 1
    assert by_name["datatype"].metrics.points == 2 * (len(specs) - 1)
    assert by_name["hostility"].metrics.points == 4  # two streams; the two probes sit before the measurement

    report = cmf.render_report(result)
    assert "| baseline |" in report and "| hostility |" in report and "| datatype |" in report
    assert "Frames identical" in report


async def test_run_comparison_reports_rejection_without_fetching(synth_stub):
    stub, pull, actors, specs = synth_stub
    stub.reject_no_type = "dataType is required"
    out: list[str] = []
    result = await cmf.run_comparison(
        stub, "SYNTH01", pull, actors, specs, [cmf.build_variants(specs)["hostility"]],
        confirm=lambda q: True, out=out.append, sleep=_no_sleep,
    )  # fmt: skip
    (v,) = result.variants
    assert v.status == "rejected" and "dataType is required" in v.detail
    assert len([c for c in stub.calls if c["dataType"] is None]) == 1  # only the probe
    assert "REJECTED" in "\n".join(out)
    assert "rejected" in cmf.render_report(result)


async def test_run_comparison_declined_spends_nothing(synth_stub):
    stub, pull, actors, specs = synth_stub
    result = await cmf.run_comparison(
        stub, "SYNTH01", pull, actors, specs, list(cmf.build_variants(specs).values()),
        confirm=lambda q: False, out=lambda s: None, sleep=_no_sleep,
    )  # fmt: skip
    assert stub.calls == [] and result.baseline is None
    assert "No baseline" in cmf.render_report(result)


async def test_run_comparison_variant_declined_after_baseline(synth_stub):
    stub, pull, actors, specs = synth_stub
    answers = iter([True, False, True])
    result = await cmf.run_comparison(
        stub, "SYNTH01", pull, actors, specs, list(cmf.build_variants(specs).values()),
        confirm=lambda q: next(answers), out=lambda s: None, sleep=_no_sleep,
    )  # fmt: skip
    assert [v.status for v in result.variants] == ["declined", "ok"]


async def test_run_comparison_page_cap_aborts_variant(synth_stub):
    stub, pull, actors, specs = synth_stub
    original = stub.query

    async def endless(gql, variables=None, **kw):
        data = await original(gql, variables, **kw)
        if variables["dataType"] is None and variables["limit"] != 5:
            data["reportData"]["report"]["events"]["nextPageTimestamp"] = variables["startTime"] + 1
        return data

    stub.query = endless
    result = await cmf.run_comparison(
        stub, "SYNTH01", pull, actors, specs, [cmf.build_variants(specs)["hostility"]],
        confirm=lambda q: True, out=lambda s: None, max_pages=3, sleep=_no_sleep,
    )  # fmt: skip
    (v,) = result.variants
    assert v.status == "aborted" and v.metrics is not None and v.metrics.pages == 6  # 2 streams x cap


# ---------------------------------------------------------------------------
# Cross-fight and heal-only variants
# ---------------------------------------------------------------------------


def _both_pulls():
    meta = RawPageCache(SYNTH_ROOT).report_meta("SYNTH01")
    pulls, _ = parse_report_meta("SYNTH01", meta)
    return pulls


def _crossfight(specs):
    pulls = _both_pulls()
    return cmf.build_crossfight(pulls, {p.fight_id: specs for p in pulls})


def test_window_of_spans_all_pulls():
    one, two = _both_pulls()
    assert cmf.window_of([two, one]) == cmf.Window((1, 2), one.start_ms, two.end_ms)
    assert cmf._window(one) == cmf.Window((1,), one.start_ms, one.end_ms)


def test_split_by_fight_counts_strays():
    rows = [{"fight": 1, "t": 1}, {"fight": 2, "t": 2}, {"fight": 3, "t": 3}, {"t": 4}, {"fight": 1, "t": 5}]
    out, stray = cmf.split_by_fight([_page(*rows[:2]), _page(*rows[2:])], (1, 2))
    assert out == {1: [rows[0], rows[4]], 2: [rows[1]]} and stray == 2


def test_keep_types_keeps_only_listed_types():
    heal, absorb = {"type": "heal"}, {"type": "absorbed"}
    assert cmf.keep_types([_page(heal, absorb), _page(heal)], ("heal",)) == [_page(heal, heal)]


def test_build_crossfight_batches_small_specs_across_pulls():
    _, _, specs = synth_inputs()
    cross = _crossfight(specs)
    keys = [s.covers[0] for s, _ in cross.streams]
    assert keys == [s.key for s in specs if s.key in cmf.BATCHED_SPEC_KEYS]
    assert "Healing" not in keys and "DamageTaken" not in keys and "Casts" not in keys
    assert all(w.fight_ids == (1, 2) for _, w in cross.streams)
    assert cross.baseline_requests == 2 * len(keys)
    deaths = next(s for s, _ in cross.streams if s.covers == ("Deaths",))
    assert deaths.label == "Deaths (2 fights)" and deaths.data_type == "Deaths"


def test_build_crossfight_splits_specs_whose_filter_differs():
    _, _, specs = synth_inputs()
    one, two = _both_pulls()
    other = [
        s.model_copy(update={"filter_expression": "ability.id IN (1)"}) if s.key == "Casts-Enemies" else s
        for s in specs
    ]
    cross = cmf.build_crossfight([one, two], {1: specs, 2: other})
    enemy = [(s, w) for s, w in cross.streams if s.covers == ("Casts-Enemies",)]
    assert [w.fight_ids for _, w in enemy] == [(1,), (2,)]
    assert [s.label for s, _ in enemy] == ["Casts-Enemies (1 fights) #1", "Casts-Enemies (1 fights) #2"]
    assert enemy[1][0].filter_expression == "ability.id IN (1)"


def test_build_crossfight_honours_spec_keys():
    _, _, specs = synth_inputs()
    pulls = _both_pulls()
    cross = cmf.build_crossfight(pulls, {p.fight_id: specs for p in pulls}, ["Deaths", "Casts"])
    assert [s.covers for s, _ in cross.streams] == [("Deaths",), ("Casts",)]


def test_select_batch_pulls():
    one, two = _both_pulls()
    pulls = [one, two]
    assert cmf.select_batch_pulls(pulls, one, "encounter") == pulls
    assert cmf.select_batch_pulls(pulls, one, "all") == pulls
    assert cmf.select_batch_pulls(pulls, one, "2,1,2") == [two, one]
    for bad in ("1", "1,9", "first"):
        with pytest.raises(SystemExit):
            cmf.select_batch_pulls(pulls, one, bad)


def test_combine_comparisons_names_differing_fights():
    same = cmf.FrameComparison(True, True, 3, 3)
    ties = cmf.FrameComparison(False, True, 2, 2)
    differ = cmf.FrameComparison(False, False, 4, 1)
    assert cmf.combine_comparisons({1: same, 2: same}) == cmf.FrameComparison(True, True, 6, 6, "")
    combined = cmf.combine_comparisons({1: same, 2: ties, 3: differ})
    assert not combined.identical and not combined.identical_ignoring_tie_order
    assert (combined.rows_baseline, combined.rows_variant) == (9, 6)
    assert "rows differ on fights [3]" in combined.note and "tie order differs on fights [2]" in combined.note


async def test_run_crossfight_matches_per_pull_baseline(synth_stub):
    stub, pull, actors, specs = synth_stub
    cross = _crossfight(specs)
    asked: list[str] = []
    result = await cmf.run_comparison(
        stub, "SYNTH01", pull, actors, specs, [], crossfight=cross,
        confirm=lambda q: asked.append(q) or True, out=lambda s: None, sleep=_no_sleep,
    )  # fmt: skip
    assert len(asked) == 1 and "crossfight" in asked[0]  # no shared baseline without single-pull variants
    assert result.baseline is None
    (v,) = result.variants
    assert v.status == "ok" and v.baseline is not None and v.metrics is not None
    n = len(cross.streams)
    assert (v.baseline.requests, v.baseline.points) == (2 * n, 2 * 2 * n)
    assert (v.metrics.requests, v.metrics.points) == (n, 2 * n)
    assert v.baseline.events == v.metrics.events
    assert v.comparison is not None and v.comparison.identical_ignoring_tie_order
    assert v.extra_rows == 0 and v.missing_rows == 0
    assert "rows naming no requested fight: 0" in v.detail
    assert sum(1 for c in stub.calls if c["fightIDs"] == [1, 2]) == n

    report = cmf.render_report(result)
    assert "| crossfight baseline |" in report and "| crossfight |" in report and "No baseline" not in report
    assert "meets the 15% bar" in report  # half the stub's points
    heading = report.splitlines()[0]
    assert "crossfight on `SYNTH01` fights 1, 2" in heading and "`SYNTH01:1`" not in heading
    assert v.fight_ids == (1, 2)


def test_report_scope_names_what_each_run_measured():
    m = cmf.Metrics()
    cross = cmf.VariantResult("crossfight", "ok", metrics=m, baseline=m, fight_ids=(4, 5))
    heal = cmf.VariantResult("healonly", "ok", metrics=m, baseline=m)
    declined = cmf.VariantResult("crossfight", "declined")
    scope = cmf.report_scope
    assert scope(cmf.Comparison("C", 26, variants=[cross])) == "crossfight on `C` fights 4, 5"
    assert scope(cmf.Comparison("C", 26, variants=[heal, cross])) == "`C:26`; crossfight on `C` fights 4, 5"
    assert scope(cmf.Comparison("C", 26, baseline=m, variants=[cross])) == "`C:26`; crossfight on `C` fights 4, 5"
    assert scope(cmf.Comparison("C", 26, variants=[declined])) == "`C:26`"


async def test_run_crossfight_counts_rows_from_unrequested_fights(synth_stub):
    stub, pull, actors, specs = synth_stub
    original = stub.query

    async def leaky(gql, variables=None, **kw):
        data = await original(gql, variables, **kw)
        if variables["fightIDs"] == [1, 2] and variables["dataType"] == "Deaths":
            data["reportData"]["report"]["events"]["data"].append({"timestamp": 1, "type": "death", "fight": 7})
        return data

    stub.query = leaky
    result = await cmf.run_comparison(
        stub, "SYNTH01", pull, actors, specs, [], crossfight=_crossfight(specs),
        confirm=lambda q: True, out=lambda s: None, sleep=_no_sleep,
    )  # fmt: skip
    (v,) = result.variants
    assert v.extra_rows == 1 and "rows naming no requested fight: 1" in v.detail


def _add_absorb(root):
    """Append an ``absorbed`` row carrying a position to fight 1's Healing page under ``root``."""
    (path,) = (root / "SYNTH01" / "1" / "Healing").glob("*/page_000.json")
    page = orjson.loads(path.read_bytes())
    heal = page["data"][0]
    page["data"].append(
        {**heal, "type": "absorbed", "timestamp": heal["timestamp"] + 1, "resourceActor": 2, "x": 1234, "y": 5678}
    )
    path.write_bytes(orjson.dumps(page))
    return len(page["data"])


async def test_run_healonly_keeps_exactly_the_heal_rows(synth_stub, tmp_path):
    stub, pull, actors, specs = synth_stub
    total = _add_absorb(tmp_path)
    result = await cmf.run_comparison(
        stub, "SYNTH01", pull, actors, specs, [], heal_filter=HEAL_FILTER,
        confirm=lambda q: True, out=lambda s: None, sleep=_no_sleep,
    )  # fmt: skip
    (v,) = result.variants
    assert v.status == "ok" and v.baseline is not None and v.metrics is not None
    assert (v.baseline.requests, v.baseline.events, v.metrics.events) == (1, total, total - 1)
    assert v.comparison is not None and v.comparison.identical
    assert v.extra_rows == 0 and v.missing_rows == 0
    assert "filter returns exactly the heal rows: True" in v.detail
    assert f"kept {total - 1} of {total} Healing rows" in v.detail
    before, after = (int(x) for x in v.detail.rsplit("samples ", 1)[1].split(" -> "))
    assert before == after + 1  # the absorbed row's position is the one lost
    probes = [c for c in stub.calls if c["limit"] == 5]
    assert len(probes) == 1 and probes[0]["filter"] == HEAL_FILTER
    assert "| healonly baseline |" in cmf.render_report(result)


async def test_run_healonly_flags_a_filter_that_keeps_too_much(synth_stub, tmp_path):
    stub, pull, actors, specs = synth_stub
    _add_absorb(tmp_path)
    stub.heal_types = None
    result = await cmf.run_comparison(
        stub, "SYNTH01", pull, actors, specs, [], heal_filter=HEAL_FILTER,
        confirm=lambda q: True, out=lambda s: None, sleep=_no_sleep,
    )  # fmt: skip
    (v,) = result.variants
    assert v.extra_rows == 1 and "filter returns exactly the heal rows: False" in v.detail
    report = cmf.render_report(result)
    assert "rows no baseline spec holds: 1" in report and "exactly the heal rows: False" in report


async def test_run_healonly_rejected_filter_fetches_nothing_more(synth_stub):
    stub, pull, actors, specs = synth_stub
    stub.reject_filters = {HEAL_FILTER: "Invalid filter expression"}
    out: list[str] = []
    result = await cmf.run_comparison(
        stub, "SYNTH01", pull, actors, specs, [], heal_filter=HEAL_FILTER,
        confirm=lambda q: True, out=out.append, sleep=_no_sleep,
    )  # fmt: skip
    (v,) = result.variants
    assert v.status == "rejected" and "Invalid filter" in v.detail
    assert len(stub.calls) == 1 and "REJECTED" in "\n".join(out)
    assert "No baseline" in cmf.render_report(result)


async def test_variant_first_fetches_the_variant_before_its_baseline(synth_stub):
    stub, pull, actors, specs = synth_stub
    await cmf.run_comparison(
        stub, "SYNTH01", pull, actors, specs, [], heal_filter=HEAL_FILTER, variant_first=True,
        confirm=lambda q: True, out=lambda s: None, sleep=_no_sleep,
    )  # fmt: skip
    probe, first, second = stub.calls
    assert probe["limit"] == 5 and first["filter"] == HEAL_FILTER and second["filter"] is None


async def test_paired_variants_declined_spend_nothing(synth_stub):
    stub, pull, actors, specs = synth_stub
    result = await cmf.run_comparison(
        stub, "SYNTH01", pull, actors, specs, [], crossfight=_crossfight(specs), heal_filter=HEAL_FILTER,
        confirm=lambda q: False, out=lambda s: None, sleep=_no_sleep,
    )  # fmt: skip
    assert stub.calls == [] and [v.status for v in result.variants] == ["declined", "declined"]


async def test_run_crossfight_page_cap_aborts(synth_stub):
    stub, pull, actors, specs = synth_stub
    original = stub.query

    async def endless(gql, variables=None, **kw):
        data = await original(gql, variables, **kw)
        if variables["fightIDs"] == [1, 2]:
            data["reportData"]["report"]["events"]["nextPageTimestamp"] = variables["startTime"] + 1
        return data

    stub.query = endless
    result = await cmf.run_comparison(
        stub, "SYNTH01", pull, actors, specs, [], crossfight=_crossfight(specs),
        confirm=lambda q: True, out=lambda s: None, max_pages=2, sleep=_no_sleep,
    )  # fmt: skip
    (v,) = result.variants
    assert v.status == "aborted" and v.baseline is not None and v.comparison is None


def test_estimates_for_paired_variants():
    _, _, specs = synth_inputs()
    text = "\n".join(cmf.crossfight_estimate(_crossfight(specs), 60))
    assert "2 pulls" in text and "documented model" in text and "ceiling" in text
    text = "\n".join(cmf.healonly_estimate("F", 60))
    assert "probe" in text and "'F'" in text and "docs/wcl-api.md" in text


def test_load_pull_reads_fixture_metadata(tmp_path):
    shutil.copytree(SYNTH_ROOT / "SYNTH01", tmp_path / "SYNTH01")
    pull, actors = cmf._load_pull(tmp_path, "SYNTH01", 2)
    assert pull.fight_id == 2 and actors
    with pytest.raises(SystemExit):
        cmf._load_pull(tmp_path, "SYNTH01", 99)
    with pytest.raises(SystemExit):
        cmf._load_pull(tmp_path, "NOPE", 1)


def test_ask_requires_typed_yes(monkeypatch):
    for answer, expected in [("yes", True), ("Y", True), ("", False), ("no", False)]:
        monkeypatch.setattr("builtins.input", lambda _prompt, a=answer: a)
        assert cmf.ask("go?") is expected

    def eof(_prompt):
        raise EOFError

    monkeypatch.setattr("builtins.input", eof)
    assert cmf.ask("go?") is False
