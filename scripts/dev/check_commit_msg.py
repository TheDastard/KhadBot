"""
scripts/dev/check_commit_msg.py

Check commit messages against the commit rules (.claude/rules/git-commits.md) and the tag registry
(scripts/dev/commit_tags.yaml).

Two modes:

- ``check_commit_msg.py FILE``: the commit-msg hook. Checks the message git is about to commit, and warns when none
  of the staged files match the chosen tag's paths. ``fixup!``, ``squash!`` and ``amend!`` commits pass so a branch
  can be folded later; CI rejects them.
- ``check_commit_msg.py --range BASE..HEAD``: checks every commit in a range (CI, ``mise run lint:commits``).

``--tags`` prints the registry. Exits 1 when any message has an error; warnings never fail.

Needs only the standard library and PyYAML, so it runs from pre-commit's own environment and from
``uv run --no-project --with pyyaml``.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path

import yaml

REGISTRY = Path(__file__).resolve().parent / "commit_tags.yaml"

# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

NAME = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*")


class RegistryError(ValueError):
    """The tag registry is malformed."""


@dataclass(frozen=True)
class Tag:
    """A tag or sub-tag from the registry."""

    name: str
    description: str
    paths: tuple[str, ...] = ()
    subtags: dict[str, Tag] = field(default_factory=dict)

    def all_paths(self) -> tuple[str, ...]:
        """This tag's paths plus every sub-tag's."""
        return self.paths + tuple(p for sub in self.subtags.values() for p in sub.paths)


def _parse_tag(name: str, raw: object, *, where: str, nested: bool) -> Tag:
    if not NAME.fullmatch(name):
        raise RegistryError(f"{where}: '{name}' is not a lowercase, hyphenated name")
    if not isinstance(raw, dict):
        raise RegistryError(f"{where}: expected a mapping")
    allowed = {"description", "paths"} if nested else {"description", "paths", "subtags"}
    unknown = set(raw) - allowed
    if unknown:
        raise RegistryError(f"{where}: unknown keys {sorted(unknown)}")
    description = raw.get("description")
    if not isinstance(description, str) or not description.strip():
        raise RegistryError(f"{where}: needs a description")
    paths = raw.get("paths") or []
    if not isinstance(paths, list) or not all(isinstance(p, str) and p for p in paths):
        raise RegistryError(f"{where}: paths must be a list of globs")
    subtags: dict[str, Tag] = {}
    raw_subtags = raw.get("subtags") or {}
    if not isinstance(raw_subtags, dict):
        raise RegistryError(f"{where}: subtags must be a mapping")
    for sub_name, sub_raw in raw_subtags.items():
        subtags[sub_name] = _parse_tag(sub_name, sub_raw, where=f"{where}.{sub_name}", nested=True)
    return Tag(name=name, description=description.strip(), paths=tuple(paths), subtags=subtags)


def load_registry(path: Path = REGISTRY) -> dict[str, Tag]:
    """Load and validate the tag registry.

    Raises:
        RegistryError: The file is not a valid registry.
    """
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or set(data) != {"tags"} or not isinstance(data["tags"], dict):
        raise RegistryError(f"{path.name}: expected a single top-level 'tags' mapping")
    return {name: _parse_tag(name, raw, where=name, nested=False) for name, raw in data["tags"].items()}


# ---------------------------------------------------------------------------
# Path globs
# ---------------------------------------------------------------------------


@cache
def _glob_regex(pattern: str) -> re.Pattern[str]:
    out = []
    i = 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:[^/]+/)*")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("".join(out))


def path_matches(path: str, patterns: tuple[str, ...] | list[str]) -> bool:
    """Whether a repo-relative POSIX path matches any glob (``*`` within a segment, ``**`` across segments)."""
    return any(_glob_regex(p).fullmatch(path) for p in patterns)


# ---------------------------------------------------------------------------
# Message checks
# ---------------------------------------------------------------------------

SUBJECT = re.compile(r"\[(?P<tag>[^\]]*)\](?:\[(?P<subtag>[^\]]*)\])? (?P<summary>[^\s\[].*)")
AUTOSQUASH = re.compile(r"(?:fixup|squash|amend)! ")
SUMMARY_SOFT_LIMIT = 72

ATTRIBUTION = [
    re.compile(r"co-authored-by:.*\b(?:claude|anthropic)\b", re.IGNORECASE),
    re.compile(r"noreply@anthropic\.com", re.IGNORECASE),
    re.compile(r"generated (?:with|by) \[?claude", re.IGNORECASE),
    re.compile(r"claude\.ai/(?:code|chat|share)", re.IGNORECASE),
]

# Plan-stage IDs: milestones, tasks, human tasks, spikes and decision rows (M1, M2.3, H4, S1, D7) and their spelled-out
# forms. S3 is the storage protocol, not a spike.
PLAN_ID = re.compile(r"\b(?!S3\b)[MHSD]\d+(?:\.\d+)*\b")
PLAN_PHRASE = re.compile(r"\b(?:milestone|spike|phase)\s+\d+\b", re.IGNORECASE)

# Sub-tags every tag accepts without listing them, and the paths they add to the tag's for the path warning.
UNIVERSAL_SUBTAGS: dict[str, tuple[str, ...]] = {
    "docs": ("docs/**", "**/*.md"),
    "tests": ("tests/**",),
    "fixtures": ("tests/fixtures/**",),
}

# An authored boss file, config/encounters/<zone_id>-<zone>/<encounter_id>-<boss>.yaml. A [raid] commit that changes
# exactly one names the boss in its summary.
BOSS_TAG = "raid"
BOSS_FILE = re.compile(r"config/encounters/[^/]+/\d+-(?P<slug>[A-Za-z0-9_-]+)\.ya?ml")


def _squash(text: str) -> str:
    """Lowercase letters and digits only, so "Ula'tek" and "ulatek" compare equal."""
    return re.sub(r"[^a-z0-9]", "", text.lower())


@dataclass
class Report:
    """Errors and warnings for one message."""

    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def clean_message(text: str) -> str:
    """Drop git's comment lines and everything below a verbose-commit scissors line."""
    lines = []
    for line in text.splitlines():
        if line.startswith("# ") and ">8" in line:
            break
        if line.startswith("#"):
            continue
        lines.append(line.rstrip())
    return "\n".join(lines).strip("\n")


def _looks_imperative(word: str) -> bool:
    lower = word.lower()
    if lower.endswith(("ed", "ing")):
        return False
    return not (lower.endswith("s") and not lower.endswith(("ss", "us", "is")))


def check_message(
    message: str,
    registry: dict[str, Tag],
    *,
    files: list[str] | None = None,
    allow_autosquash: bool = False,
) -> Report:
    """Check one commit message.

    Args:
        message: The message, already cleaned of git's comment lines.
        registry: The tag registry.
        files: The commit's changed paths; when given, a tag none of them match is a warning.
        allow_autosquash: Let ``fixup!``/``squash!``/``amend!`` messages through (the local hook).
    """
    report = Report()
    lines = message.splitlines()
    if not lines or not lines[0].strip():
        report.errors.append("the message is empty")
        return report
    subject = lines[0]

    if AUTOSQUASH.match(subject):
        if not allow_autosquash:
            report.errors.append("fixup!/squash!/amend! commits must be folded before they land")
        return report

    match = SUBJECT.fullmatch(subject)
    if not match:
        report.errors.append(f"the subject must read '[tag] Summary' or '[tag][subtag] Summary': {subject!r}")
        return report
    tag_name, sub_name, summary = match["tag"], match["subtag"], match["summary"]

    tag = registry.get(tag_name)
    if tag_name == "KB":
        report.errors.append("drop the [KB] prefix: subjects start with the tag, '[tag][subtag] Summary'")
        return report
    if tag is None:
        report.errors.append(f"unknown tag '{tag_name}' (see scripts/dev/commit_tags.yaml)")
    elif sub_name is not None and sub_name not in tag.subtags and sub_name not in UNIVERSAL_SUBTAGS:
        known = ", ".join(sorted(tag.subtags)) or "none"
        universal = ", ".join(UNIVERSAL_SUBTAGS)
        report.errors.append(
            f"unknown sub-tag '{sub_name}' for '{tag_name}' (registered: {known}; any tag also takes {universal})"
        )

    first_word = summary.split()[0]
    if not first_word[0].isupper():
        report.errors.append(f"the summary must start with a capitalised verb: {first_word!r}")
    elif not _looks_imperative(first_word):
        report.errors.append(f"the summary must start with an imperative verb (Add, not Adds/Added): {first_word!r}")
    if summary.endswith("."):
        report.errors.append("the summary must not end with a period")
    if len(subject) > SUMMARY_SOFT_LIMIT:
        report.warnings.append(f"the subject is {len(subject)} characters; aim for {SUMMARY_SOFT_LIMIT} or fewer")

    if len(lines) > 1 and lines[1].strip():
        report.errors.append("leave a blank line between the subject and the body")
    if not any(line.strip() for line in lines[1:]):
        report.errors.append("add a body saying what changed and why")

    for pattern in ATTRIBUTION:
        if pattern.search(message):
            report.errors.append("remove the AI attribution (trailer, footer or session link)")
            break
    plan_ids = sorted(
        {m.group(0) for m in PLAN_ID.finditer(message)} | {m.group(0) for m in PLAN_PHRASE.finditer(message)}
    )
    if plan_ids:
        report.errors.append(f"name what the change does instead of plan IDs: {', '.join(plan_ids)}")

    if files and tag is not None:
        owner = tag.subtags.get(sub_name) if sub_name else None
        if owner is not None and owner.paths:
            patterns = owner.paths
        elif sub_name in UNIVERSAL_SUBTAGS:
            patterns = tag.all_paths() + UNIVERSAL_SUBTAGS[sub_name]
        else:
            patterns = tag.all_paths()
        if patterns and not any(path_matches(f, patterns) for f in files):
            label = f"[{tag_name}][{sub_name}]" if sub_name else f"[{tag_name}]"
            report.warnings.append(f"none of the changed files match {label}'s paths; check the tag")

    if files and tag_name == BOSS_TAG:
        bosses = {m["slug"] for f in files if (m := BOSS_FILE.fullmatch(f))}
        if len(bosses) == 1:
            (slug,) = bosses
            if _squash(slug) not in _squash(summary):
                report.warnings.append(f"the commit changes one boss file; name the boss ({slug}) in the summary")
    return report


# ---------------------------------------------------------------------------
# Git
# ---------------------------------------------------------------------------


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True, encoding="utf-8").stdout


def staged_files() -> list[str]:
    """Paths staged for the commit being written."""
    return [p for p in _git("diff", "--cached", "--name-only", "-z").split("\0") if p]


def commits_in(rev_range: str) -> list[str]:
    """Commits in a range, oldest first."""
    return _git("rev-list", "--reverse", "--no-merges", rev_range).split()


def commit_message(sha: str) -> str:
    return _git("log", "-1", "--format=%B", sha)


def commit_files(sha: str) -> list[str]:
    out = _git("diff-tree", "--root", "--no-commit-id", "--name-only", "-r", "-z", sha)
    return [p for p in out.split("\0") if p]


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def _print_report(label: str, report: Report) -> None:
    for error in report.errors:
        print(f"{label}: error: {error}", file=sys.stderr)
    for warning in report.warnings:
        print(f"{label}: warning: {warning}", file=sys.stderr)


def _print_tags(registry: dict[str, Tag]) -> None:
    for tag in registry.values():
        print(f"[{tag.name}]  {tag.description}")
        for sub in tag.subtags.values():
            print(f"  [{tag.name}][{sub.name}]  {sub.description}")
    print(f"\nEvery tag also takes these sub-tags for its own parts: {', '.join(UNIVERSAL_SUBTAGS)}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[1] if __doc__ else None)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("file", nargs="?", help="commit message file (commit-msg hook)")
    group.add_argument("--range", dest="rev_range", help="check every commit in BASE..HEAD")
    group.add_argument("--tags", action="store_true", help="print the registered tags")
    args = parser.parse_args(argv)

    registry = load_registry()
    if args.tags:
        _print_tags(registry)
        return 0

    failed = False
    if args.rev_range:
        shas = commits_in(args.rev_range)
        for sha in shas:
            message = commit_message(sha)
            report = check_message(clean_message(message), registry, files=commit_files(sha))
            _print_report(f"{sha[:10]} {message.splitlines()[0] if message else ''}", report)
            failed |= bool(report.errors)
        print(f"checked {len(shas)} commit(s) in {args.rev_range}")
    else:
        message = clean_message(Path(args.file).read_text(encoding="utf-8"))
        report = check_message(message, registry, files=staged_files(), allow_autosquash=True)
        _print_report("commit message", report)
        failed = bool(report.errors)
        if failed:
            print(
                "see .claude/rules/git-commits.md; `python scripts/dev/check_commit_msg.py --tags` lists the tags",
                file=sys.stderr,
            )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
