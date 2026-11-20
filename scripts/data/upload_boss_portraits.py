"""
scripts/data/upload_boss_portraits.py

Converts the boss portraits the registry names (``config/registries/boss_art.yaml``) from the game client's journal
export to PNG and writes them to the blob store under ``art/portraits/``, so the API can serve them
(``docs/game-art.md``). The export folder is ``<WoW>/_retail_/BlizzardInterfaceArt/Interface/ENCOUNTERJOURNAL``, made
by the client's ``exportInterfaceFiles art`` command; each portrait is ``UI-EJ-BOSS-<name>.blp`` (a ``.png`` of the
same name is accepted too). The store is the one ``wiring.blob_store()`` picks, so with the usual settings the same
command fills the local data directory or, with ``KHADBOT_STORAGE=s3``, the hosted bucket. A portrait whose converted
bytes match the stored object is skipped. The exported art is never committed.

Needs Pillow, which the ``dev`` group installs, so ``uv run`` has it:

    uv run python scripts/data/upload_boss_portraits.py <export folder> [--dry-run]

Exits 1 when the registry names a portrait the export doesn't hold.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from khadbot.api.boss_art import DEFAULT_BOSS_ART_PATH, BossArt, load_boss_art, portrait_key
from khadbot.errors import ConfigurationError
from khadbot.storage import BlobStore

FILE_PREFIX = "UI-EJ-BOSS-"
SOURCE_SUFFIXES = (".blp", ".png")

# ---------------------------------------------------------------------------
# Conversion
# ---------------------------------------------------------------------------


@dataclass
class UploadReport:
    """What a run did, by portrait name."""

    written: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)


def find_source(export_dir: Path, portrait: str) -> Path | None:
    """The export file for ``portrait``: ``UI-EJ-BOSS-<portrait>.blp`` (else ``.png``), matched ignoring case."""
    rank = {f"{FILE_PREFIX}{portrait}{suffix}".casefold(): i for i, suffix in enumerate(SOURCE_SUFFIXES)}
    found = [(rank[p.name.casefold()], p) for p in export_dir.iterdir() if p.name.casefold() in rank]
    return min(found, key=lambda item: item[0])[1] if found else None


def to_png(source: Path) -> bytes:
    """The image at ``source`` (BLP or PNG, read by Pillow) as RGBA PNG bytes."""
    from PIL import Image  # dev group only: the API never converts art

    with Image.open(source) as image:
        out = io.BytesIO()
        image.convert("RGBA").save(out, format="PNG")
    return out.getvalue()


def upload_portraits(
    export_dir: Path, registry: Mapping[int, BossArt], blobs: BlobStore, *, dry_run: bool = False
) -> UploadReport:
    """Convert and store each distinct portrait the registry names; ``dry_run`` converts but writes nothing."""
    report = UploadReport()
    for portrait in sorted({b.portrait for b in registry.values() if b.portrait}):
        source = find_source(export_dir, portrait)
        if source is None:
            report.missing.append(portrait)
            continue
        png = to_png(source)
        key = portrait_key(portrait)
        existing = blobs.get(key)
        if existing is not None and hashlib.sha256(existing).digest() == hashlib.sha256(png).digest():
            report.skipped.append(portrait)
            continue
        if not dry_run:
            blobs.put(key, png)
        report.written.append(portrait)
    return report


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Upload the registry's boss portraits to the blob store.")
    parser.add_argument("export_dir", type=Path, help="the export's ENCOUNTERJOURNAL folder")
    parser.add_argument("--registry", type=Path, default=DEFAULT_BOSS_ART_PATH, help="boss art registry file")
    parser.add_argument("--dry-run", action="store_true", help="convert and compare, write nothing")
    args = parser.parse_args(argv)
    if not args.export_dir.is_dir():
        print(f"not a folder: {args.export_dir}", file=sys.stderr)
        return 2
    try:
        registry = load_boss_art(args.registry)
    except ConfigurationError as exc:
        print(exc, file=sys.stderr)
        return 2
    from khadbot import wiring

    report = upload_portraits(args.export_dir, registry, wiring.blob_store(), dry_run=args.dry_run)
    verb = "would write" if args.dry_run else "wrote"
    print(f"{verb} {len(report.written)}: {', '.join(report.written) or '-'}")
    print(f"skipped (unchanged) {len(report.skipped)}: {', '.join(report.skipped) or '-'}")
    print(f"not found {len(report.missing)}: {', '.join(report.missing) or '-'}")
    return 1 if report.missing else 0


if __name__ == "__main__":
    sys.exit(main())
