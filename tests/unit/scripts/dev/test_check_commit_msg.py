"""
tests/unit/scripts/dev/test_check_commit_msg.py

scripts/dev/check_commit_msg.py, the commit-message checker: subject format, tags and sub-tags, summary wording,
body, AI attribution, plan IDs, autosquash commits and the path warning. Uses a small registry, not the real one.
"""

from __future__ import annotations

import pytest
from helpers.scripts import load_script

check = load_script("dev", "check_commit_msg")

REGISTRY_YAML = """
tags:
  encounter:
    description: Encounter mode
    paths: [src/khadbot/encounter/*.py]
    subtags:
      ingest:
        description: Ingest
        paths: [src/khadbot/encounter/ingest/**]
      eval:
        description: Eval harness
  docs:
    description: Docs
    paths: [docs/**]
  raid:
    description: Boss files
    paths: [config/encounters/**]
    subtags:
      venomous-abyss:
        description: Zone 53
        paths: [config/encounters/53-the_venomous_abyss/**]
"""

ULATEK = "config/encounters/53-the_venomous_abyss/3492-ulatek.yaml"

BODY = "\n\nSay what changed and why."


@pytest.fixture
def registry(tmp_path):
    path = tmp_path / "tags.yaml"
    path.write_text(REGISTRY_YAML, encoding="utf-8")
    return check.load_registry(path)


def run(message: str, registry, **kwargs):
    return check.check_message(check.clean_message(message), registry, **kwargs)


# ---------------------------------------------------------------------------
# Subject
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "subject",
    ["[encounter][ingest] Add boss health to pulls", "[encounter] Wire the mode", "[docs] Document test sessions"],
)
def test_valid_subjects(registry, subject):
    report = run(subject + BODY, registry)
    assert report.errors == []


@pytest.mark.parametrize(
    ("subject", "error"),
    [
        ("[KB][encounter-ingest] Add boss health", "drop the [KB] prefix"),
        ("Add boss health", "must read"),
        ("[encounter][ingest]Add boss health", "must read"),
        ("[encounter] [ingest] Add boss health", "must read"),
        ("[web] Add a page", "unknown tag 'web'"),
        ("[encounter][annotate] Add phases", "unknown sub-tag 'annotate'"),
        ("[docs][wcl] Add notes", "registered: none"),
        ("[encounter] add boss health", "capitalised"),
        ("[encounter] Adds boss health", "imperative"),
        ("[encounter] Added boss health", "imperative"),
        ("[encounter] Adding boss health", "imperative"),
        ("[encounter] Add boss health.", "period"),
    ],
)
def test_subject_errors(registry, subject, error):
    report = run(subject + BODY, registry)
    assert any(error in e for e in report.errors), report.errors


@pytest.mark.parametrize("verb", ["Address", "Focus", "Process"])
def test_imperative_verbs_ending_in_s_pass(registry, verb):
    assert run(f"[encounter] {verb} the gap" + BODY, registry).errors == []


def test_long_subject_only_warns(registry):
    report = run("[encounter][ingest] Add " + "x" * 80 + BODY, registry)
    assert report.errors == []
    assert any("characters" in w for w in report.warnings)


# ---------------------------------------------------------------------------
# Body
# ---------------------------------------------------------------------------


def test_body_is_required(registry):
    assert any("add a body" in e for e in run("[encounter] Add a thing", registry).errors)


def test_blank_line_after_subject(registry):
    assert any("blank line" in e for e in run("[encounter] Add a thing\nNo gap here.", registry).errors)


def test_git_comments_and_scissors_are_dropped(registry):
    message = (
        "[encounter] Add a thing\n\nWhy it matters.\n# Please enter the commit message\n"
        "# ------------------------ >8 ------------------------\ndiff --git a b\nAdds M1 Co-Authored-By: Claude\n"
    )
    assert run(message, registry).errors == []


@pytest.mark.parametrize(
    "line",
    [
        "Co-Authored-By: Claude Opus <noreply@anthropic.com>",
        "Generated with [Claude Code](https://claude.com/claude-code)",
        "https://claude.ai/code/session_123",
    ],
)
def test_ai_attribution_is_rejected(registry, line):
    report = run("[encounter] Add a thing\n\nWhy.\n\n" + line, registry)
    assert any("attribution" in e for e in report.errors)


@pytest.mark.parametrize(
    "text", ["Covers M2.3 and H4.", "Adds decision D72.", "Finishes milestone 1.", "From spike 2."]
)
def test_plan_ids_are_rejected(registry, text):
    report = run("[encounter] Add a thing\n\n" + text, registry)
    assert any("plan IDs" in e for e in report.errors)


@pytest.mark.parametrize("text", ["Writes to S3 and the S3-compatible bucket.", "Report H7z1fqDXjRTFnbtg.", "Uses d3."])
def test_plan_id_lookalikes_pass(registry, text):
    assert run("[encounter] Add a thing\n\n" + text, registry).errors == []


# ---------------------------------------------------------------------------
# Autosquash and paths
# ---------------------------------------------------------------------------


def test_autosquash_passes_the_hook_but_not_ci(registry):
    message = "fixup! [encounter] Add a thing"
    assert run(message, registry, allow_autosquash=True).errors == []
    assert any("folded" in e for e in run(message, registry).errors)


def test_path_mismatch_warns(registry):
    report = run("[encounter][ingest] Add a thing" + BODY, registry, files=["docs/notes.md"])
    assert report.errors == []
    assert any("match [encounter][ingest]" in w for w in report.warnings)


def test_path_match_is_quiet(registry):
    report = run("[encounter][ingest] Add a thing" + BODY, registry, files=["src/khadbot/encounter/ingest/fetch.py"])
    assert report.warnings == []


def test_subtag_without_paths_uses_its_tags(registry):
    report = run("[encounter][eval] Add a thing" + BODY, registry, files=["src/khadbot/encounter/store.py"])
    assert report.warnings == []


# ---------------------------------------------------------------------------
# Universal sub-tags
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("subtag", ["docs", "tests", "fixtures"])
def test_universal_subtags_need_no_registration(registry, subtag):
    assert run(f"[encounter][{subtag}] Add a thing" + BODY, registry).errors == []


def test_unknown_subtag_error_names_the_universal_ones(registry):
    report = run("[encounter][bosses] Add a thing" + BODY, registry)
    assert any("any tag also takes docs, tests, fixtures" in e for e in report.errors)


def test_docs_subtag_counts_documentation_paths(registry):
    report = run("[encounter][docs] Record a finding" + BODY, registry, files=["docs/follow-ups.md"])
    assert report.warnings == []


def test_tests_subtag_still_warns_outside_tests(registry):
    report = run("[encounter][tests] Add a helper" + BODY, registry, files=["apps/web/page.tsx"])
    assert any("check the tag" in w for w in report.warnings)


# ---------------------------------------------------------------------------
# Boss names
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("summary", ["Fail Ula'tek's Acidic Expulsion past 2 stacks", "Add ulatek mythic entries"])
def test_boss_named_in_summary_is_quiet(registry, summary):
    report = run(f"[raid][venomous-abyss] {summary}" + BODY, registry, files=[ULATEK])
    assert report.warnings == []


def test_one_boss_file_without_its_name_warns(registry):
    report = run("[raid][venomous-abyss] Fail Acidic Expulsion past 2 stacks" + BODY, registry, files=[ULATEK])
    assert report.errors == []
    assert any("name the boss (ulatek)" in w for w in report.warnings)


def test_several_boss_files_need_no_name(registry):
    other = "config/encounters/53-the_venomous_abyss/3493-other_boss.yaml"
    report = run("[raid][venomous-abyss] Move a spell to the registry" + BODY, registry, files=[ULATEK, other])
    assert report.warnings == []


def test_boss_names_are_only_checked_for_the_raid_tag(registry):
    report = run("[encounter] Rename a field" + BODY, registry, files=[ULATEK, "src/khadbot/encounter/models.py"])
    assert report.warnings == []
