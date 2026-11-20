"""
apps/web/e2e/support/boss-art.py

Puts generated test portraits into the local data directory's blob store, for the local e2e suite. Writes a small solid
PNG named UI-EJ-BOSS-<portrait>.png for every portrait the boss art registry names, then runs the real upload script
(scripts/data/upload_boss_portraits.py) on that folder, so the art route serves them as it would a game export. The
images are generated here; no game art is involved. KHADBOT_DATA_DIR picks the data directory.
Usage: boss-art.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from PIL import Image

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO / "scripts" / "data"))

from upload_boss_portraits import main as upload  # noqa: E402

from khadbot.api.boss_art import load_boss_art  # noqa: E402

with tempfile.TemporaryDirectory() as folder:
    for entry in load_boss_art().values():
        if entry.portrait:
            Image.new("RGBA", (128, 64), (200, 40, 160, 255)).save(Path(folder) / f"UI-EJ-BOSS-{entry.portrait}.png")
    sys.exit(upload([folder]))
