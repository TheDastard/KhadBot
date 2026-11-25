"""
tests/unit/scripts/dev/test_check_docs.py

scripts/dev/check_docs.py: each check on small inputs, and the repository itself passing, so ``mise run check``
fails while a doc breaks the limits in ``.claude/rules/docs.md``.
"""

from __future__ import annotations

from pathlib import Path

from helpers.scripts import load_script

check = load_script("dev", "check_docs")

ROOT = Path(__file__).resolve().parents[4]


# ---------------------------------------------------------------------------
# The repository
# ---------------------------------------------------------------------------


def test_repository_docs_pass():
    problems = check.check_repo(ROOT)
    assert problems == [], "\n".join(str(p) for p in problems)


# ---------------------------------------------------------------------------
# Size
# ---------------------------------------------------------------------------


def test_doc_over_line_limit():
    text = "line\n" * (check.MAX_DOC_LINES + 1)
    assert [p.message.split(",")[0] for p in check.check_size("docs/big.md", text)] == ["401 lines"]


def test_doc_over_byte_limit():
    text = "x" * (check.MAX_DOC_BYTES + 1) + "\n"
    assert len(check.check_size("docs/wide.md", text)) == 1


def test_claude_md_limit_and_other_files_unchecked():
    big = "x" * (check.MAX_CLAUDE_MD_BYTES + 1)
    assert len(check.check_size("CLAUDE.md", big)) == 1
    assert check.check_size("README.md", big * 10) == []


# ---------------------------------------------------------------------------
# Rule scope
# ---------------------------------------------------------------------------


def test_rule_needs_paths_unless_always_loaded():
    scoped = '---\npaths:\n  - "src/**"\n---\n\n# Rule\n'
    assert check.check_rule_scope(".claude/rules/area.md", scoped) == []
    assert len(check.check_rule_scope(".claude/rules/area.md", "# Rule\n")) == 1
    assert check.check_rule_scope(".claude/rules/git-commits.md", "# Rule\n") == []
    assert check.check_rule_scope(".claude/skills/x/SKILL.md", "# Skill\n") == []


# ---------------------------------------------------------------------------
# Decisions and follow-ups
# ---------------------------------------------------------------------------


def _row(decision: str, choice: str = "Choice") -> str:
    return f"| {decision} | Name | {choice} | Why | 2026-11-18 |"


def test_decision_rows():
    rel = "docs/decisions.md"
    assert check.check_decisions(rel, _row("D1") + "\n" + _row("D2")) == []
    assert "cells" in check.check_decisions(rel, "| D3 | Name | Choice | 2026-11-18 |")[0].message
    long = check.check_decisions(rel, _row("D4", "x" * check.MAX_DECISION_ROW_CHARS))
    assert "characters" in long[0].message
    twice = check.check_decisions(rel, _row("D5") + "\n" + _row("D5"))
    assert [(p.line, "twice" in p.message) for p in twice] == [(2, True)]
    assert check.check_decisions(rel, _row("D17") + "\n" + _row("D17")) == []


def test_follow_ups_have_no_resolved_section():
    rel = "docs/follow-ups.md"
    assert check.check_follow_ups(rel, "# Follow-ups\n\n## Open\n") == []
    assert [p.line for p in check.check_follow_ups(rel, "# Follow-ups\n\n## Resolved\n")] == [3]


def test_encounter_follow_ups_have_no_resolved_section():
    rel = "docs/encounter/follow-ups/venomous-abyss.md"
    assert check.check_follow_ups(rel, "# Raid\n\n## Boss (1), Heroic\n") == []
    assert [p.line for p in check.check_follow_ups(rel, "# Raid\n\n### Resolved\n")] == [3]
    assert check.check_follow_ups("docs/encounter/pipeline.md", "## Resolved\n") == []


# ---------------------------------------------------------------------------
# References
# ---------------------------------------------------------------------------


def test_references(tmp_path: Path):
    (tmp_path / "docs" / "sub").mkdir(parents=True)
    (tmp_path / "docs" / "sub" / "here.md").write_text("x", encoding="utf-8")
    text = "\n".join(
        [
            "[ok](sub/here.md#part) [web](https://example.com) [anchor](#top)",
            "[gone](sub/missing.md)",
            "`docs/sub/` `docs/sub/here.md` `docs/old.md` `.claude/worktrees/x/`",
            "```",
            "[in a fence](nowhere.md) `docs/fenced.md`",
            "```",
        ]
    )
    problems = check.check_references("docs/index.md", text, tmp_path)
    assert [(p.line, p.message) for p in problems] == [
        (2, "link to sub/missing.md, which doesn't exist"),
        (3, "`docs/old.md` doesn't exist"),
    ]
