"""
simc/fetch.py

Download SimC's generated client tables from the SimC repository.

A branch, tag or commit is resolved to its commit first, and the files go under ``<root>/<commit>/``, so a
download is pinned and reproducible and each commit is fetched once.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import httpx

from khadbot.simc.client_data import SIMC_FILES

SIMC_REPO = "simulationcraft/simc"
GENERATED_DIR = "engine/dbc/generated"


def fetch_tables(ref: str, root: Path, *, progress: Callable[[str], None] | None = None) -> tuple[Path, str]:
    """Download the SimC tables at ``ref`` into ``root/<commit>``; returns the directory and the commit.

    Files already on disk are kept. Each file is written to a temporary name and renamed, so an interrupted
    download never leaves a partial table behind.
    """
    with httpx.Client(timeout=120, follow_redirects=True) as client:
        resp = client.get(f"https://api.github.com/repos/{SIMC_REPO}/commits/{ref}")
        resp.raise_for_status()
        commit = str(resp.json()["sha"])
        out = root / commit
        for name in SIMC_FILES:
            target = out / name
            if target.is_file():
                continue
            if progress:
                progress(name)
            data = client.get(f"https://raw.githubusercontent.com/{SIMC_REPO}/{commit}/{GENERATED_DIR}/{name}")
            data.raise_for_status()
            out.mkdir(parents=True, exist_ok=True)
            tmp = target.with_name(name + ".tmp")
            tmp.write_bytes(data.content)
            tmp.replace(target)
    return out, commit
