"""
tests/unit/scripts/dev/test_commit_tags.py

scripts/dev/commit_tags.yaml, the commit tag registry: it loads, and every git-tracked file belongs to some tag, so
a new package or folder fails here until it is given a tag or sub-tag.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from helpers.scripts import load_script

check = load_script("dev", "check_commit_msg")

ROOT = Path(__file__).resolve().parents[4]


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def test_registry_loads():
    registry = check.load_registry()
    assert "encounter" in registry
    assert "ingest" in registry["encounter"].subtags


def test_every_tag_but_repo_owns_paths():
    """A tag with no paths can't be checked against a commit's files. `repo` keeps its paths on its sub-tags."""
    registry = check.load_registry()
    assert [name for name, tag in registry.items() if not tag.all_paths()] == []


def _tracked_files() -> list[str]:
    try:
        out = subprocess.run(
            ["git", "-C", str(ROOT), "ls-files", "-z"], check=True, capture_output=True, text=True, encoding="utf-8"
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("not a git checkout")
    return [p for p in out.split("\0") if p]


def test_every_tracked_file_has_a_tag():
    registry = check.load_registry()
    patterns = tuple(p for tag in registry.values() for p in tag.all_paths())
    orphans = [f for f in _tracked_files() if not check.path_matches(f, patterns)]
    assert orphans == [], (
        "these files match no tag in scripts/dev/commit_tags.yaml; add their paths to a tag or sub-tag "
        "(or a new one): " + ", ".join(orphans[:20])
    )


# ---------------------------------------------------------------------------
# Registry validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("tags:\n  Encounter:\n    description: x\n", "lowercase"),
        ("tags:\n  a:\n    paths: [x]\n", "description"),
        ("tags:\n  a:\n    description: x\n    owner: me\n", "unknown keys"),
        (
            "tags:\n  a:\n    description: x\n    subtags:\n      b:\n        description: y\n        subtags: {}\n",
            "unknown",
        ),
        ("tags:\n  a:\n    description: x\n    paths: src/\n", "list of globs"),
        ("tag: {}\n", "top-level"),
    ],
)
def test_registry_rejects_malformed_files(tmp_path, body, message):
    path = tmp_path / "tags.yaml"
    path.write_text(body, encoding="utf-8")
    with pytest.raises(check.RegistryError, match=message):
        check.load_registry(path)


@pytest.mark.parametrize(
    ("path", "pattern", "matches"),
    [
        ("src/khadbot/encounter/ingest/fetch.py", "src/khadbot/encounter/ingest/**", True),
        ("src/khadbot/encounter/ingest/fetch.py", "src/khadbot/encounter/*.py", False),
        ("src/khadbot/encounter/store.py", "src/khadbot/encounter/*.py", True),
        ("apps/web/playwright.local.config.ts", "apps/web/playwright*.config.ts", True),
        ("deploy/Dockerfile", "deploy/**/Dockerfile", True),
        ("deploy/a/b/Dockerfile", "deploy/**/Dockerfile", True),
        ("main.py", "src/khadbot/*.py", False),
    ],
)
def test_globs(path, pattern, matches):
    assert check.path_matches(path, [pattern]) is matches
