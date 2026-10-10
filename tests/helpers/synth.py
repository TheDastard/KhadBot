"""
tests/helpers/synth.py

Paths and filter inputs for the synthetic SYNTH01 fixture (see tests/fixtures/wcl/README.md).
"""

from __future__ import annotations

from pathlib import Path

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
GOLDEN = Path(__file__).resolve().parents[1] / "golden"

SYNTH_REGISTRY_IDS = [900301, 900302]
SYNTH_ENEMY_CAST_IDS = {90001: [900101, 900201, 900401, 900501, 900601]}
SYNTH_ENCOUNTER_FILE = FIXTURES / "encounters" / "synth.yaml"
SYNTH_REGISTRY_FILE = FIXTURES / "registries" / "synth_cooldowns.yaml"
SYNTH_ENCOUNTER_ID = 90001
