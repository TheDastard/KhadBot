"""
tests/helpers/encounter/synth.py

Paths and filter inputs for the synthetic SYNTH01 fixture (see tests/fixtures/wcl/README.md).
"""

from __future__ import annotations

from pathlib import Path

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
GOLDEN = Path(__file__).resolve().parents[2] / "golden"

SYNTH_REGISTRY_IDS = [900301, 900302]
SYNTH_ENEMY_CAST_IDS = {90001: [900101, 900201, 900401, 900501, 900601]}
SYNTH_ENCOUNTER_FILE = FIXTURES / "encounters" / "synth.yaml"
SYNTH_REGISTRY_FILE = FIXTURES / "registries" / "synth_cooldowns.yaml"
SYNTH_ENCOUNTER_ID = 90001
SYNTH_ENCOUNTER_RELPATH = "9000-synthetic_citadel/90001-synthetic_boss.yaml"


def install_synth_encounter(root: Path, text: str | None = None) -> Path:
    """Write the synthetic encounter file (or ``text``) at its canonical path under ``root``; return that path."""
    path = root / SYNTH_ENCOUNTER_RELPATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(SYNTH_ENCOUNTER_FILE.read_text(encoding="utf-8") if text is None else text, encoding="utf-8")
    return path
