"""
tests/unit/scripts/dev/test_check_env.py

scripts/dev/check_env.py: compares an environment file with its example by name, appends missing names commented
out, and never prints a value.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from helpers.scripts import load_script

check_env = load_script("dev", "check_env")

EXAMPLE = """\
# --- Section one ---
# A comment about A.
A_ONE=placeholder
# A_TWO=2

# --- Section two ---
# B_ONE=http://localhost  # trailing note
export B_TWO=x
not a setting = here
#   uv run python -c "X=1"
"""


# ---------------------------------------------------------------------------
# Parsing and comparing
# ---------------------------------------------------------------------------


def test_env_keys_reads_set_commented_and_exported_names():
    assert check_env.env_keys(EXAMPLE) == ["A_ONE", "A_TWO", "B_ONE", "B_TWO"]


def test_commented_out_names_count_as_present(tmp_path: Path):
    example, copy = tmp_path / ".env.example", tmp_path / ".env"
    example.write_text(EXAMPLE, encoding="utf-8")
    copy.write_text("A_ONE=real-secret\n# A_TWO=\nB_ONE=1\nB_TWO=2\n", encoding="utf-8")
    assert check_env.compare(example, copy).in_sync


def test_compare_reports_missing_and_extra(tmp_path: Path):
    example, copy = tmp_path / ".env.example", tmp_path / ".env"
    example.write_text(EXAMPLE, encoding="utf-8")
    copy.write_text("A_ONE=real-secret\nOLD_NAME=1\n", encoding="utf-8")
    drift = check_env.compare(example, copy)
    assert drift.missing == ["A_TWO", "B_ONE", "B_TWO"]
    assert drift.extra == ["OLD_NAME"]


def test_append_missing_adds_commented_lines_under_their_sections(tmp_path: Path):
    example, copy = tmp_path / ".env.example", tmp_path / ".env"
    example.write_text(EXAMPLE, encoding="utf-8")
    copy.write_text("A_ONE=real-secret", encoding="utf-8")  # no trailing newline
    check_env.append_missing(check_env.compare(example, copy))

    text = copy.read_text(encoding="utf-8")
    assert text.startswith("A_ONE=real-secret\n")
    assert "# A_TWO=2\n" in text
    assert "# --- Section two ---\n# B_ONE=http://localhost  # trailing note\n# export B_TWO=x\n" in text
    assert check_env.compare(example, copy).in_sync
    # Every appended name is commented out, so nothing is set that wasn't before.
    added = text.split("\n", 1)[1]
    assert all(line == "" or line.startswith("#") for line in added.splitlines())


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    # Git exports GIT_DIR, GIT_INDEX_FILE and friends to `rebase --exec` commands and hooks. Left set, every git call
    # below would act on the real checkout instead of tmp_path (git init would mark it bare).
    for name in [n for n in os.environ if n.startswith("GIT_")]:
        monkeypatch.delenv(name)
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "deploy").mkdir()
    (tmp_path / ".env.example").write_text("A=placeholder\n# B=\n", encoding="utf-8")
    (tmp_path / "deploy" / "dev.env.example").write_text("C=1\n", encoding="utf-8")
    (tmp_path / "deploy" / "dev.env").write_text("C=1\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_cli_reports_names_never_values(repo: Path, capsys: pytest.CaptureFixture[str]):
    (repo / ".env").write_text("A=very-secret-value\nGONE=other-secret\n", encoding="utf-8")
    assert check_env.main([]) == 0
    out = capsys.readouterr().out
    assert "missing: B" in out and "GONE" in out
    assert "secret" not in out


def test_cli_strict_fails_on_drift(repo: Path):
    (repo / ".env").write_text("A=x\n", encoding="utf-8")
    assert check_env.main(["--strict"]) == 1
    (repo / ".env").write_text("A=x\n# B=\n", encoding="utf-8")
    assert check_env.main(["--strict"]) == 0


def test_cli_fix_appends_and_leaves_values(repo: Path):
    (repo / ".env").write_text("A=x\n", encoding="utf-8")
    assert check_env.main(["--fix"]) == 0
    assert (repo / ".env").read_text(encoding="utf-8").startswith("A=x\n")
    assert check_env.main(["--strict"]) == 0


def test_hook_is_quiet_when_in_sync_or_missing_and_never_fails(repo: Path, capsys: pytest.CaptureFixture[str]):
    assert check_env.main(["--hook"]) == 0  # no .env yet
    (repo / ".env").write_text("A=x\n# B=\n", encoding="utf-8")
    assert check_env.main(["--hook"]) == 0
    assert capsys.readouterr() == ("", "")
    (repo / ".env").write_text("A=x\n", encoding="utf-8")
    assert check_env.main(["--hook"]) == 0
    assert "missing: B" in capsys.readouterr().err


def test_a_worktree_reads_the_main_checkouts_env(repo: Path, tmp_path_factory: pytest.TempPathFactory):
    for args in (["add", "."], ["-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-qm", "x"]):
        subprocess.run(["git", *args], cwd=repo, check=True)
    worktree = tmp_path_factory.mktemp("wt") / "wt"
    subprocess.run(["git", "worktree", "add", "-q", str(worktree)], cwd=repo, check=True)

    here, main = check_env.checkout_roots(worktree)
    assert here == worktree.resolve()
    assert main == repo.resolve()
    assert check_env.pairs(here, main)[0] == (worktree.resolve() / ".env.example", repo.resolve() / ".env")
