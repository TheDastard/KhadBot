"""
tests/unit/simc/test_simc_fetch.py

Downloading SimC's tables: a ref is pinned to its commit, files on disk are kept, and HTTP errors raise.
All HTTP is mocked with respx.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx

from khadbot.simc.client_data import SIMC_FILES
from khadbot.simc.fetch import GENERATED_DIR, SIMC_REPO, fetch_tables

COMMIT = "0123456789abcdef0123456789abcdef01234567"
_API = f"https://api.github.com/repos/{SIMC_REPO}/commits/midnight"


def _raw(name: str) -> str:
    return f"https://raw.githubusercontent.com/{SIMC_REPO}/{COMMIT}/{GENERATED_DIR}/{name}"


@respx.mock
def test_fetch_pins_ref_to_commit(tmp_path: Path) -> None:
    respx.get(_API).respond(json={"sha": COMMIT})
    for name in SIMC_FILES:
        respx.get(_raw(name)).respond(content=f"// {name}".encode())
    seen: list[str] = []

    out, commit = fetch_tables("midnight", tmp_path, progress=seen.append)

    assert (out, commit) == (tmp_path / COMMIT, COMMIT)
    assert sorted(p.name for p in out.iterdir()) == sorted(SIMC_FILES)
    assert (out / "trait_data.inc").read_text() == "// trait_data.inc"
    assert seen == list(SIMC_FILES)


@respx.mock
def test_fetch_keeps_files_already_on_disk(tmp_path: Path) -> None:
    respx.get(_API).respond(json={"sha": COMMIT})
    (tmp_path / COMMIT).mkdir()
    for name in SIMC_FILES:
        (tmp_path / COMMIT / name).write_text("cached")
    raw = respx.get(url__startswith="https://raw.githubusercontent.com/").respond(content=b"new")

    out, _ = fetch_tables("midnight", tmp_path)

    assert not raw.called
    assert (out / "sc_spell_data.inc").read_text() == "cached"


@respx.mock
def test_fetch_raises_on_http_error_and_leaves_no_partial_file(tmp_path: Path) -> None:
    respx.get(_API).respond(json={"sha": COMMIT})
    respx.get(url__startswith="https://raw.githubusercontent.com/").respond(404)

    with pytest.raises(httpx.HTTPStatusError):
        fetch_tables("midnight", tmp_path)
    assert not (tmp_path / COMMIT).exists()


@respx.mock
def test_fetch_raises_on_unknown_ref(tmp_path: Path) -> None:
    respx.get(_API).respond(422)
    with pytest.raises(httpx.HTTPStatusError):
        fetch_tables("midnight", tmp_path)
