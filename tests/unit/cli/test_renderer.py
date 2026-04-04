"""
tests/unit/cli/test_renderer.py

Unit tests for cli/renderer.py.

What we test:
  - parse_tier(): boundary conditions across the full percentile range
  - render_parse(): label composition and style assignment
  - _mplus_score_style(): tier boundaries
  - render_character_card(): conditional row logic (score + raid progress)
  - render_simc_results(): delta sign/colour logic, baseline row handling
  - render_parse_table(): per-row tier mapping
  - render_error() / render_warning(): console.print called with content
  - _build_langsmith_url(): URL structure and failure cases
  - render_langsmith_footer(): silent-skip and print cases

What we don't test:
  - The exact Rich markup/panel structure emitted — asserting Rich's rendering
    output is testing Rich, not our code. We assert on the data transformations
    and console.print call counts instead.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from khadbot.cli.renderer import (
    _build_langsmith_url,
    _mplus_score_style,
    parse_tier,
    render_character_card,
    render_error,
    render_langsmith_footer,
    render_parse,
    render_parse_table,
    render_simc_results,
    render_warning,
)

# ===========================================================================
# parse_tier
# ===========================================================================


class TestParseTier:
    """Boundary value tests for the percentile → tier mapping."""

    @pytest.mark.parametrize(
        "percentile,expected",
        [
            # Exact tier boundaries
            (95, "legendary"),
            (75, "epic"),
            (50, "rare"),
            (25, "uncommon"),
            (5, "common"),
            (0, "poor"),
            # Just above each boundary
            (96, "legendary"),
            (76, "epic"),
            (51, "rare"),
            (26, "uncommon"),
            (6, "common"),
            # Just below each boundary
            (94, "epic"),
            (74, "rare"),
            (49, "uncommon"),
            (24, "common"),
            (4, "poor"),
            # Extremes
            (100, "legendary"),
            (1, "poor"),
        ],
    )
    def test_tier_boundaries(self, percentile: int, expected: str) -> None:
        assert parse_tier(percentile) == expected


# ===========================================================================
# render_parse
# ===========================================================================


class TestRenderParse:
    """render_parse returns a Rich Text object with the correct label and style."""

    def test_percentile_only(self) -> None:
        text = render_parse(82)
        assert "82th" in text.plain
        assert text.style == "quality.epic"

    def test_with_spec(self) -> None:
        text = render_parse(55, spec="Fire Mage")
        assert "Fire Mage" in text.plain
        assert "55th" in text.plain

    def test_with_boss(self) -> None:
        text = render_parse(30, boss="Fyrakk")
        assert "Fyrakk" in text.plain
        assert "30th" in text.plain

    def test_with_spec_and_boss(self) -> None:
        text = render_parse(97, spec="Fire Mage", boss="Fyrakk")
        assert "Fire Mage" in text.plain
        assert "97th" in text.plain
        assert "Fyrakk" in text.plain

    def test_legendary_style(self) -> None:
        text = render_parse(95)
        assert text.style == "quality.legendary"

    def test_poor_style(self) -> None:
        text = render_parse(3)
        assert text.style == "quality.poor"

    @pytest.mark.parametrize(
        "percentile,expected_style",
        [
            (95, "quality.legendary"),
            (75, "quality.epic"),
            (50, "quality.rare"),
            (25, "quality.uncommon"),
            (5, "quality.common"),
            (0, "quality.poor"),
        ],
    )
    def test_style_matches_tier(self, percentile: int, expected_style: str) -> None:
        text = render_parse(percentile)
        assert text.style == expected_style


# ===========================================================================
# _mplus_score_style
# ===========================================================================


class TestMplusScoreStyle:
    """Tier boundaries for M+ score colour mapping."""

    @pytest.mark.parametrize(
        "score,expected",
        [
            (3000, "quality.legendary"),
            (3001, "quality.legendary"),
            (2999, "quality.epic"),
            (2500, "quality.epic"),
            (2499, "quality.rare"),
            (2000, "quality.rare"),
            (1999, "quality.uncommon"),
            (1500, "quality.uncommon"),
            (1499, "quality.common"),
            (0, "quality.common"),
        ],
    )
    def test_score_boundaries(self, score: float, expected: str) -> None:
        assert _mplus_score_style(score) == expected


# ===========================================================================
# render_langsmith_footer
# ===========================================================================

# ===========================================================================
# _build_langsmith_url
# ===========================================================================


def _mock_langsmith(org_id: str, project_id: str, api_url: str = "https://api.smith.langchain.com"):
    """Return a mock langsmith module wired with the given IDs."""
    mock_run = MagicMock()
    mock_run.session_id = project_id

    mock_client = MagicMock()
    mock_client.api_url = api_url
    mock_client.read_run.return_value = mock_run
    mock_client._get_tenant_id.return_value = org_id

    mock_module = MagicMock()
    mock_module.Client.return_value = mock_client
    return mock_module


class TestBuildLangsmithUrl:
    def test_returns_none_when_sdk_unavailable(self) -> None:
        with patch.dict("sys.modules", {"langsmith": None}):
            assert _build_langsmith_url("some-run-id") is None

    def test_returns_none_when_client_raises(self) -> None:
        mock_module = MagicMock()
        mock_module.Client.side_effect = Exception("unreachable")
        with patch.dict("sys.modules", {"langsmith": mock_module}):
            assert _build_langsmith_url("some-run-id") is None

    def test_returns_none_when_read_run_raises(self) -> None:
        mock_client = MagicMock()
        mock_client.read_run.side_effect = Exception("run not found")
        mock_module = MagicMock()
        mock_module.Client.return_value = mock_client
        with patch.dict("sys.modules", {"langsmith": mock_module}):
            assert _build_langsmith_url("some-run-id") is None

    def test_url_contains_org_id(self) -> None:
        with patch.dict("sys.modules", {"langsmith": _mock_langsmith("org-abc", "proj-xyz")}):
            url = _build_langsmith_url("run-123")
        assert "/o/org-abc/" in url

    def test_url_contains_project_id(self) -> None:
        with patch.dict("sys.modules", {"langsmith": _mock_langsmith("org-abc", "proj-xyz")}):
            url = _build_langsmith_url("run-123")
        assert "/projects/p/proj-xyz" in url

    def test_url_contains_run_id_as_peek_param(self) -> None:
        with patch.dict("sys.modules", {"langsmith": _mock_langsmith("org-abc", "proj-xyz")}):
            url = _build_langsmith_url("run-123")
        assert "peek=run-123" in url

    def test_url_does_not_contain_peeked_trace_param(self) -> None:
        with patch.dict("sys.modules", {"langsmith": _mock_langsmith("org-abc", "proj-xyz")}):
            url = _build_langsmith_url("run-123")
        assert "peeked_trace" not in url

    def test_api_subdomain_stripped_from_base(self) -> None:
        """https://api.smith.langchain.com → https://smith.langchain.com"""
        with patch.dict("sys.modules", {"langsmith": _mock_langsmith("org", "proj")}):
            url = _build_langsmith_url("run-123")
        assert url.startswith("https://smith.langchain.com")
        assert "api." not in url

    def test_full_url_structure(self) -> None:
        """Verify the complete URL matches the known-good LangSmith format."""
        run_id = "019cf2a3-d75c-7e21-b0fa-e2fbded3f5ef"
        org_id = "741b9d09-3459-4bd3-8931-bea413518c14"
        proj_id = "24b4b203-caf0-44eb-991e-e426e9f891b8"
        with patch.dict("sys.modules", {"langsmith": _mock_langsmith(org_id, proj_id)}):
            url = _build_langsmith_url(run_id)
        expected = f"https://smith.langchain.com/o/{org_id}/projects/p/{proj_id}?peek={run_id}"
        assert url == expected


# ===========================================================================
# render_langsmith_footer
# ===========================================================================


class TestRenderLangsmithFooter:
    def test_none_run_id_is_silent(self) -> None:
        with patch("khadbot.cli.renderer.console") as mock_console:
            render_langsmith_footer(None)
        mock_console.print.assert_not_called()

    def test_empty_string_run_id_is_silent(self) -> None:
        with patch("khadbot.cli.renderer.console") as mock_console:
            render_langsmith_footer("")
        mock_console.print.assert_not_called()

    def test_url_build_failure_is_silent(self) -> None:
        """If _build_langsmith_url returns None, nothing is printed."""
        with patch("khadbot.cli.renderer._build_langsmith_url", return_value=None):
            with patch("khadbot.cli.renderer.console") as mock_console:
                render_langsmith_footer("some-run-id")
        mock_console.print.assert_not_called()

    def test_prints_url_when_build_succeeds(self) -> None:
        """When _build_langsmith_url returns a URL, console.print is called."""
        with patch(
            "khadbot.cli.renderer._build_langsmith_url",
            return_value="https://smith.langchain.com/o/x/projects/p/y?peek=z",
        ):
            with patch("khadbot.cli.renderer.console") as mock_console:
                render_langsmith_footer("z")
        assert mock_console.print.called
        printed = str(mock_console.print.call_args_list)
        assert "https://smith.langchain.com" in printed


# ===========================================================================
# render_character_card
# ===========================================================================


class TestRenderCharacterCard:
    """
    render_character_card conditionally adds M+ score and raid progress rows.
    We assert on console.print being called (the card renders without error)
    and on the _mplus_score_style logic by checking the right style is chosen.
    """

    def test_renders_without_optional_fields(self) -> None:
        with patch("khadbot.cli.renderer.console") as mock_console:
            render_character_card("Thrall", "Stormrage", "us", "Enhancement Shaman")
        assert mock_console.print.called

    def test_renders_with_all_fields(self) -> None:
        with patch("khadbot.cli.renderer.console") as mock_console:
            render_character_card(
                "Thrall",
                "Stormrage",
                "us",
                "Enhancement Shaman",
                mythic_plus_score=2847.0,
                raid_progress="9/9 M",
            )
        assert mock_console.print.called

    def test_region_uppercased(self) -> None:
        """Region is displayed uppercased — verify the table row is built correctly."""

        with patch("khadbot.cli.renderer.Table") as mock_table_cls:
            mock_table = MagicMock()
            mock_table_cls.grid.return_value = mock_table
            with patch("khadbot.cli.renderer.console"):
                render_character_card("Thrall", "Stormrage", "eu", "Enhancement Shaman")

        # The first add_row call should contain the uppercased region
        first_call_args = mock_table.add_row.call_args_list[0][0]
        assert "EU" in first_call_args[1]

    def test_score_row_omitted_when_none(self) -> None:
        with patch("khadbot.cli.renderer.Table") as mock_table_cls:
            mock_table = MagicMock()
            mock_table_cls.grid.return_value = mock_table
            with patch("khadbot.cli.renderer.console"):
                render_character_card("Thrall", "Stormrage", "us", "Enhancement Shaman", mythic_plus_score=None)

        row_labels = [c[0][0] for c in mock_table.add_row.call_args_list]
        assert "M+ Score" not in row_labels

    def test_score_row_present_when_provided(self) -> None:
        with patch("khadbot.cli.renderer.Table") as mock_table_cls:
            mock_table = MagicMock()
            mock_table_cls.grid.return_value = mock_table
            with patch("khadbot.cli.renderer.console"):
                render_character_card("Thrall", "Stormrage", "us", "Enhancement Shaman", mythic_plus_score=2847.0)

        row_labels = [c[0][0] for c in mock_table.add_row.call_args_list]
        assert "M+ Score" in row_labels

    def test_raid_row_omitted_when_none(self) -> None:
        with patch("khadbot.cli.renderer.Table") as mock_table_cls:
            mock_table = MagicMock()
            mock_table_cls.grid.return_value = mock_table
            with patch("khadbot.cli.renderer.console"):
                render_character_card("Thrall", "Stormrage", "us", "Enhancement Shaman")

        row_labels = [c[0][0] for c in mock_table.add_row.call_args_list]
        assert "Raid" not in row_labels

    def test_raid_row_present_when_provided(self) -> None:
        with patch("khadbot.cli.renderer.Table") as mock_table_cls:
            mock_table = MagicMock()
            mock_table_cls.grid.return_value = mock_table
            with patch("khadbot.cli.renderer.console"):
                render_character_card("Thrall", "Stormrage", "us", "Enhancement Shaman", raid_progress="9/9 M")

        row_labels = [c[0][0] for c in mock_table.add_row.call_args_list]
        assert "Raid" in row_labels


# ===========================================================================
# render_simc_results
# ===========================================================================


class TestRenderSimcResults:
    """
    render_simc_results has real branching logic:
      - Row 0 always shows "—" for delta regardless of delta field
      - Positive delta gets "+" prefix and uncommon (green) style
      - Negative delta gets no prefix and poor (white) style
      - delta=None on non-baseline rows also shows "—"
    """

    def _baseline(self) -> dict:
        return {"label": "Current Gear", "mean_dps": 480_000, "min_dps": 460_000, "max_dps": 500_000, "delta": None}

    def _upgrade(self, delta: float | None) -> dict:
        return {"label": "With Trinket", "mean_dps": 490_000, "min_dps": 470_000, "max_dps": 510_000, "delta": delta}

    def test_renders_without_error(self) -> None:
        with patch("khadbot.cli.renderer.console"):
            render_simc_results([self._baseline()])

    def test_baseline_row_shows_em_dash(self) -> None:
        """First row always gets "—" regardless of its delta value."""
        with patch("khadbot.cli.renderer.Table") as mock_table_cls:
            mock_table = MagicMock()
            mock_table_cls.return_value = mock_table
            with patch("khadbot.cli.renderer.console"):
                render_simc_results([self._baseline()])

        _, _, _, delta_text = mock_table.add_row.call_args_list[0][0]
        assert delta_text.plain == "—"

    def test_positive_delta_has_plus_prefix(self) -> None:
        with patch("khadbot.cli.renderer.Table") as mock_table_cls:
            mock_table = MagicMock()
            mock_table_cls.return_value = mock_table
            with patch("khadbot.cli.renderer.console"):
                render_simc_results([self._baseline(), self._upgrade(delta=10_000)])

        _, _, _, delta_text = mock_table.add_row.call_args_list[1][0]
        assert delta_text.plain.startswith("+")

    def test_positive_delta_style(self) -> None:
        with patch("khadbot.cli.renderer.Table") as mock_table_cls:
            mock_table = MagicMock()
            mock_table_cls.return_value = mock_table
            with patch("khadbot.cli.renderer.console"):
                render_simc_results([self._baseline(), self._upgrade(delta=10_000)])

        _, _, _, delta_text = mock_table.add_row.call_args_list[1][0]
        assert delta_text.style == "quality.uncommon"

    def test_negative_delta_has_no_plus_prefix(self) -> None:
        with patch("khadbot.cli.renderer.Table") as mock_table_cls:
            mock_table = MagicMock()
            mock_table_cls.return_value = mock_table
            with patch("khadbot.cli.renderer.console"):
                render_simc_results([self._baseline(), self._upgrade(delta=-5_000)])

        _, _, _, delta_text = mock_table.add_row.call_args_list[1][0]
        assert not delta_text.plain.startswith("+")
        assert "-" in delta_text.plain

    def test_negative_delta_style(self) -> None:
        with patch("khadbot.cli.renderer.Table") as mock_table_cls:
            mock_table = MagicMock()
            mock_table_cls.return_value = mock_table
            with patch("khadbot.cli.renderer.console"):
                render_simc_results([self._baseline(), self._upgrade(delta=-5_000)])

        _, _, _, delta_text = mock_table.add_row.call_args_list[1][0]
        assert delta_text.style == "quality.poor"

    def test_none_delta_on_non_baseline_shows_em_dash(self) -> None:
        with patch("khadbot.cli.renderer.Table") as mock_table_cls:
            mock_table = MagicMock()
            mock_table_cls.return_value = mock_table
            with patch("khadbot.cli.renderer.console"):
                render_simc_results([self._baseline(), self._upgrade(delta=None)])

        _, _, _, delta_text = mock_table.add_row.call_args_list[1][0]
        assert delta_text.plain == "—"


# ===========================================================================
# render_parse_table
# ===========================================================================


class TestRenderParseTable:
    """
    render_parse_table maps each row's percentile through parse_tier to
    produce a styled Text cell. The key logic is the per-row tier mapping.
    """

    def _parse_row(self, percentile: int) -> dict:
        return {"boss": "Fyrakk", "spec": "Fire Mage", "percentile": percentile, "dps": 480_000, "ilvl": 489}

    def test_renders_without_error(self) -> None:
        with patch("khadbot.cli.renderer.console"):
            render_parse_table([self._parse_row(75)])

    def test_legendary_parse_cell_style(self) -> None:
        with patch("khadbot.cli.renderer.Table") as mock_table_cls:
            mock_table = MagicMock()
            mock_table_cls.return_value = mock_table
            with patch("khadbot.cli.renderer.console"):
                render_parse_table([self._parse_row(95)])

        _, _, parse_text, _, _ = mock_table.add_row.call_args_list[0][0]
        assert parse_text.style == "quality.legendary"

    def test_poor_parse_cell_style(self) -> None:
        with patch("khadbot.cli.renderer.Table") as mock_table_cls:
            mock_table = MagicMock()
            mock_table_cls.return_value = mock_table
            with patch("khadbot.cli.renderer.console"):
                render_parse_table([self._parse_row(3)])

        _, _, parse_text, _, _ = mock_table.add_row.call_args_list[0][0]
        assert parse_text.style == "quality.poor"

    def test_multiple_rows_each_styled_independently(self) -> None:
        with patch("khadbot.cli.renderer.Table") as mock_table_cls:
            mock_table = MagicMock()
            mock_table_cls.return_value = mock_table
            with patch("khadbot.cli.renderer.console"):
                render_parse_table([self._parse_row(95), self._parse_row(3)])

        styles = [c[0][2].style for c in mock_table.add_row.call_args_list]
        assert styles == ["quality.legendary", "quality.poor"]

    def test_dps_formatted_with_commas(self) -> None:
        with patch("khadbot.cli.renderer.Table") as mock_table_cls:
            mock_table = MagicMock()
            mock_table_cls.return_value = mock_table
            with patch("khadbot.cli.renderer.console"):
                render_parse_table([self._parse_row(75)])

        _, _, _, dps_str, _ = mock_table.add_row.call_args_list[0][0]
        assert "," in dps_str


# ===========================================================================
# render_error / render_warning
# ===========================================================================


class TestRenderErrorAndWarning:
    """
    These functions are thin console.print wrappers, but they're worth
    smoke-testing: they appear in the REPL error handler path and a bad
    import or argument error would silently swallow exceptions at runtime.
    """

    def test_render_error_calls_print(self) -> None:
        with patch("khadbot.cli.renderer.console") as mock_console:
            render_error("Something went wrong")
        assert mock_console.print.called

    def test_render_error_custom_title(self) -> None:
        with patch("khadbot.cli.renderer.console") as mock_console:
            render_error("Timeout", title="SimC Error")
        panel = mock_console.print.call_args_list[0][0][0]
        assert "SimC Error" in panel.title

    def test_render_warning_calls_print(self) -> None:
        with patch("khadbot.cli.renderer.console") as mock_console:
            render_warning("Rate limit approaching")
        assert mock_console.print.called

    def test_render_warning_includes_message(self) -> None:
        with patch("khadbot.cli.renderer.console") as mock_console:
            render_warning("Rate limit approaching")
        printed = str(mock_console.print.call_args_list)
        assert "Rate limit approaching" in printed
