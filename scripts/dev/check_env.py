"""
scripts/dev/check_env.py

Compare the gitignored environment files with the committed examples they were copied from, by variable name only:

- ``.env`` (the main checkout's, which worktrees share through ``load_dotenv``) against ``.env.example``;
- ``deploy/dev.env`` (this checkout's) against ``deploy/dev.env.example``.

A name counts as present whether it is set or commented out (``# KHADBOT_FOO=``), so a copy that keeps the
example's commented-out settings is in sync. The check reports names the example has that the copy lacks (a new
setting) and names the copy has that the example lacks (a removed or misspelt setting). It never reads or prints a
value, so its output is safe to show anywhere.

Usage:

- ``check_env.py``: report; exits 0 (``--strict``: exits 1 when anything is out of sync).
- ``check_env.py --fix``: also append each missing name to the copy, commented out as the example has it, under its
  section heading. Values are never filled in.
- ``check_env.py --hook``: the post-checkout and post-merge git hook. Quiet when in sync or when a copy doesn't exist
  yet (``scripts/dev/setup.ps1`` creates them), and never fails.

Needs only the standard library, so it runs from pre-commit's own environment as well as ``uv run``.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

# A setting line: ``NAME=...``, ``# NAME=...`` or ``export NAME=...``. Prose lines start with something else.
KEY_LINE = re.compile(r"^\s*(?:#\s*)?(?:export\s+)?([A-Z][A-Z0-9_]*)\s*=")
# A section heading in the examples: ``# --- LLM Backend ---``.
SECTION_LINE = re.compile(r"^#\s*---.*---\s*$")

# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def env_keys(text: str) -> list[str]:
    """Return the variable names a dotenv text sets or lists commented out, in first-seen order."""
    seen: dict[str, None] = {}
    for line in text.splitlines():
        if match := KEY_LINE.match(line):
            seen.setdefault(match.group(1), None)
    return list(seen)


def _example_lines(text: str) -> dict[str, tuple[str | None, str]]:
    """Map each name to (its section heading, its first line in the example, commented out)."""
    out: dict[str, tuple[str | None, str]] = {}
    section: str | None = None
    for line in text.splitlines():
        if SECTION_LINE.match(line):
            section = line.strip()
        elif match := KEY_LINE.match(line):
            stripped = line.strip()
            out.setdefault(match.group(1), (section, stripped if stripped.startswith("#") else f"# {stripped}"))
    return out


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Drift:
    """How one copy differs from its example, by name."""

    example: Path
    copy: Path
    missing: list[str]  # in the example, not in the copy
    extra: list[str]  # in the copy, not in the example

    @property
    def in_sync(self) -> bool:
        return not self.missing and not self.extra


def compare(example: Path, copy: Path) -> Drift:
    """Compare ``copy`` with ``example`` by variable name. ``copy`` must exist."""
    want = env_keys(example.read_text(encoding="utf-8"))
    have = env_keys(copy.read_text(encoding="utf-8"))
    have_set, want_set = set(have), set(want)
    return Drift(
        example=example,
        copy=copy,
        missing=[k for k in want if k not in have_set],
        extra=[k for k in have if k not in want_set],
    )


def append_missing(drift: Drift) -> None:
    """Append each missing name to the copy, commented out, grouped under the example's section headings."""
    if not drift.missing:
        return
    lines_by_key = _example_lines(drift.example.read_text(encoding="utf-8"))
    text = drift.copy.read_text(encoding="utf-8")
    block = ["", f"# Added from {drift.example.name} by scripts/dev/check_env.py on {date.today().isoformat()}."]
    last_section: str | None = None
    for key in drift.missing:
        section, line = lines_by_key[key]
        if section and section != last_section:
            block += ["", section]
            last_section = section
        block.append(line)
    sep = "" if not text or text.endswith("\n") else "\n"
    drift.copy.write_text(text + sep + "\n".join(block) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Locating the files
# ---------------------------------------------------------------------------


def _git(*args: str, cwd: Path) -> str:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


def checkout_roots(cwd: Path) -> tuple[Path, Path]:
    """Return (this checkout's root, the main checkout's root). They are the same outside a worktree."""
    here = Path(_git("rev-parse", "--show-toplevel", cwd=cwd)).resolve()
    common = Path(_git("rev-parse", "--git-common-dir", cwd=cwd))
    if not common.is_absolute():
        common = here / common
    return here, common.resolve().parent


def pairs(here: Path, main: Path) -> list[tuple[Path, Path]]:
    """The (example, copy) pairs to check. ``.env`` lives only in the main checkout; ``dev.env`` in each one."""
    return [
        (here / ".env.example", main / ".env"),
        (here / "deploy" / "dev.env.example", here / "deploy" / "dev.env"),
    ]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _report(drift: Drift, *, fixed: bool) -> list[str]:
    lines = [f"{drift.copy} is out of sync with {drift.example.name}:"]
    if drift.missing:
        verb = "added, commented out" if fixed else "missing"
        lines.append(f"  {verb}: {', '.join(drift.missing)}")
    if drift.extra:
        lines.append(f"  not in {drift.example.name} (removed or misspelt?): {', '.join(drift.extra)}")
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[1] if __doc__ else None)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--fix", action="store_true", help="append missing names to the copy, commented out")
    mode.add_argument("--hook", action="store_true", help="git hook: quiet when in sync, never fails")
    parser.add_argument("--strict", action="store_true", help="exit 1 when anything is out of sync")
    args = parser.parse_args(argv)

    try:
        here, main_root = checkout_roots(Path.cwd())
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"check_env: not in a git checkout ({exc})", file=sys.stderr)
        return 0 if args.hook else 1

    out: list[str] = []
    drifted = False
    for example, copy in pairs(here, main_root):
        if not example.exists():
            continue
        if not copy.exists():
            if not args.hook:
                out.append(f"{copy} doesn't exist; scripts/dev/setup.ps1 creates it from {example.name}.")
            continue
        drift = compare(example, copy)
        if drift.in_sync:
            if not args.hook:
                out.append(f"{copy} is in sync with {example.name}.")
            continue
        drifted = True
        if args.fix:
            append_missing(drift)
        out += _report(drift, fixed=args.fix)

    if drifted and not args.fix:
        out.append("Run `mise run env:fix` to append the missing names (commented out), then fill in their values.")
    if out:
        print("\n".join(out), file=sys.stderr if args.hook else sys.stdout)
    return 1 if (args.strict and drifted and not args.fix) else 0


if __name__ == "__main__":
    sys.exit(main())
