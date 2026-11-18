"""
scripts/dev/check_docs.py

Check the limits in ``.claude/rules/docs.md`` that a script can check, over the git-tracked Markdown files:

- every doc under ``docs/`` stays under ``MAX_DOC_LINES`` lines and ``MAX_DOC_BYTES`` bytes;
- ``CLAUDE.md`` stays under ``MAX_CLAUDE_MD_BYTES``, since every session loads it;
- every rule in ``.claude/rules/`` has ``paths:`` frontmatter, except the ones in ``ALWAYS_LOADED_RULES``;
- every row of the ``docs/decisions.md`` table has five cells, a ``D<n>`` ID used once, and at most
  ``MAX_DECISION_ROW_CHARS`` characters;
- ``docs/follow-ups.md`` has no "Resolved" section (fixed items are deleted);
- every relative Markdown link, and every backticked ``docs/`` or ``.claude/`` path, names a file or folder that
  exists, so a move or rename can't leave a stale reference behind.

Sizes count ``\\n`` line endings, so a CRLF checkout measures the same as CI.

Usage: ``check_docs.py`` prints each problem as ``path:line: message`` and exits 1 when there is any.
``tests/unit/scripts/dev/test_check_docs.py`` runs it over the repository, so ``mise run check`` enforces it.

Needs only the standard library.
"""

from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

MAX_DOC_LINES = 400
MAX_DOC_BYTES = 40 * 1024
MAX_CLAUDE_MD_BYTES = 10 * 1024
MAX_DECISION_ROW_CHARS = 600

# Rules loaded in every session: every session may commit.
ALWAYS_LOADED_RULES = frozenset({"git-commits.md"})
# IDs that were used twice before IDs were checked; they stay as they are because docs cite them.
KNOWN_DUPLICATE_DECISIONS = frozenset({"D17"})
# Folders that exist only at run time (gitignored), so a doc may name them without them being in the checkout.
RUNTIME_PATHS = (".claude/worktrees",)

DECISION_ROW = re.compile(r"^\|\s*(D\d+)\s*\|")
LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
BACKTICK_PATH = re.compile(r"`((?:docs|\.claude)/[A-Za-z0-9_./-]*)`")
FRONTMATTER_PATHS = re.compile(r"\A---\n(?:.*\n)*?paths:\s*\n(?:.*\n)*?---\n")

# ---------------------------------------------------------------------------
# Problems
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Problem:
    path: str
    line: int
    message: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: {self.message}"


def _read(root: Path, rel: str) -> str:
    return (root / rel).read_text(encoding="utf-8").replace("\r\n", "\n")


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------


def check_size(rel: str, text: str) -> list[Problem]:
    """A doc under ``docs/`` stays under the line and byte limits; ``CLAUDE.md`` under its own byte limit."""
    size = len(text.encode("utf-8"))
    if rel == "CLAUDE.md":
        if size > MAX_CLAUDE_MD_BYTES:
            return [Problem(rel, 1, f"{size} bytes, over {MAX_CLAUDE_MD_BYTES}: move area detail into a rule")]
        return []
    if not rel.startswith("docs/"):
        return []
    problems = []
    lines = text.count("\n")
    if lines > MAX_DOC_LINES:
        problems.append(Problem(rel, 1, f"{lines} lines, over {MAX_DOC_LINES}: split it by subject into a folder"))
    if size > MAX_DOC_BYTES:
        problems.append(Problem(rel, 1, f"{size} bytes, over {MAX_DOC_BYTES}: split it by subject into a folder"))
    return problems


def check_rule_scope(rel: str, text: str) -> list[Problem]:
    """A rule loads only for its paths, unless it is one of the always-loaded rules."""
    if not (rel.startswith(".claude/rules/") and rel.endswith(".md")):
        return []
    if Path(rel).name in ALWAYS_LOADED_RULES or FRONTMATTER_PATHS.match(text):
        return []
    return [Problem(rel, 1, "no `paths:` frontmatter, so it loads in every session; scope it or make it a skill")]


def check_decisions(rel: str, text: str) -> list[Problem]:
    """Each decision row has five cells, a unique ID and stays short."""
    if rel != "docs/decisions.md":
        return []
    problems = []
    seen: set[str] = set()
    for number, line in enumerate(text.split("\n"), start=1):
        match = DECISION_ROW.match(line)
        if not match:
            continue
        decision = match.group(1)
        cells = line.strip().strip("|").split("|")
        if len(cells) != 5:
            problems.append(
                Problem(rel, number, f"{decision} has {len(cells)} cells, not 5 (# | Decision | Choice | Why | Date)")
            )
        if len(line) > MAX_DECISION_ROW_CHARS:
            problems.append(
                Problem(
                    rel,
                    number,
                    f"{decision} is {len(line)} characters, over {MAX_DECISION_ROW_CHARS}: "
                    "keep the choice and reason, link the reference doc for the rest",
                )
            )
        if decision in seen and decision not in KNOWN_DUPLICATE_DECISIONS:
            problems.append(Problem(rel, number, f"{decision} is used twice: number a new row after the highest ID"))
        seen.add(decision)
    return problems


def check_follow_ups(rel: str, text: str) -> list[Problem]:
    """Fixed follow-ups are deleted, not moved to a Resolved section."""
    if rel != "docs/follow-ups.md":
        return []
    return [
        Problem(rel, number, "a Resolved section: delete fixed items instead")
        for number, line in enumerate(text.split("\n"), start=1)
        if re.match(r"^#+\s+Resolved\b", line)
    ]


def check_references(rel: str, text: str, root: Path) -> list[Problem]:
    """Relative links resolve from the file; backticked ``docs/`` and ``.claude/`` paths resolve from the root."""
    problems = []
    base = (root / rel).parent
    in_fence = False
    for number, line in enumerate(text.split("\n"), start=1):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
        if in_fence:
            continue
        for target in LINK.findall(line):
            if re.match(r"^[a-z][a-z0-9+.-]*:", target) or target.startswith("#"):
                continue
            path = target.split("#", 1)[0]
            if path and not (base / path).exists():
                problems.append(Problem(rel, number, f"link to {target}, which doesn't exist"))
        for path in BACKTICK_PATH.findall(line):
            if path.startswith(RUNTIME_PATHS):
                continue
            if not (root / path.rstrip("/")).exists():
                problems.append(Problem(rel, number, f"`{path}` doesn't exist"))
    return problems


# ---------------------------------------------------------------------------
# Running
# ---------------------------------------------------------------------------


def tracked_markdown(root: Path) -> list[str]:
    """The git-tracked Markdown files, as POSIX paths relative to ``root``, skipping test fixtures."""
    out = subprocess.run(
        ["git", "ls-files", "-z", "--", "*.md"], cwd=root, capture_output=True, check=True
    ).stdout.decode("utf-8")
    return sorted(p for p in out.split("\0") if p and not p.startswith("tests/") and (root / p).is_file())


def check_file(rel: str, root: Path) -> list[Problem]:
    text = _read(root, rel)
    return [
        *check_size(rel, text),
        *check_rule_scope(rel, text),
        *check_decisions(rel, text),
        *check_follow_ups(rel, text),
        *check_references(rel, text, root),
    ]


def check_repo(root: Path = ROOT) -> list[Problem]:
    return [problem for rel in tracked_markdown(root) for problem in check_file(rel, root)]


def main() -> int:
    problems = check_repo()
    for problem in problems:
        print(problem)
    if problems:
        print(f"{len(problems)} problem(s); the rules are in .claude/rules/docs.md", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
