"""
tests/helpers/encounter/synth.py

Paths and filter inputs for the synthetic SYNTH01 fixture (see tests/fixtures/encounter/wcl/README.md).
"""

from __future__ import annotations

from pathlib import Path

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "encounter"  # encounter fixtures only
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


# ---------------------------------------------------------------------------
# Friendly damage
# ---------------------------------------------------------------------------

# A friendly-damage registry for SYNTH01, with fake IDs like the rest of the fixture.
SYNTH_FRIENDLY_DAMAGE = {
    "abilities": [
        {"ability_id": 901001, "name": "Synth Self Burn", "class": "self_inflicted", "verified": True},
        {"ability_id": 901002, "name": "Synth Link", "class": "redistribution", "verified": False},
    ]
}
SYNTH_UNCLASSIFIED_ID = 901003  # friendly-source damage the registry doesn't list

# (t_ms, source_id, target_id, ability_id, amount) added to fight 1: Alphadps (3) burns herself 1 s before her
# 8 s death; Betadps (4) takes Self Burn from the Environment (-1); Fido (6, Alphadps's pet) links damage to
# Healz (2); Gammadps (5) hits Tankyboi (1) with an ability no file names.
SYNTH_FRIENDLY_ROWS = [
    (7_000, 3, 3, 901001, 2_000),
    (12_000, -1, 4, 901001, 3_000),
    (15_000, 6, 2, 901002, 5_000),
    (16_000, 5, 1, SYNTH_UNCLASSIFIED_ID, 4_000),
]


def add_friendly_damage(store, fight: int = 1, rows: list[tuple] | None = None) -> None:
    """Add friendly damage rows (default ``SYNTH_FRIENDLY_ROWS``) to a stored SYNTH01 pull, keeping time order."""
    import polars as pl

    from khadbot.encounter.ingest.normalize import events_hash
    from khadbot.encounter.store import EVENT_SCHEMA

    pull = next(p for p in store.pulls("SYNTH01") if p.fight_id == fight)
    friendly = set(pull.friendly_player_ids) | {6}
    added = []
    for t, source, target, ability, amount in SYNTH_FRIENDLY_ROWS if rows is None else rows:
        row = {name: None for name in EVENT_SCHEMA}
        row.update(
            seq=0,
            t_ms=t,
            data_type="DamageTaken",
            type="damage",
            source_id=source,
            target_id=target,
            ability_id=ability,
            amount=amount,
            source_friendly=source in friendly,
            target_friendly=target in friendly,
        )
        added.append(row)
    df = pl.concat([store.events(pull.pull_key), pl.DataFrame(added, schema=EVENT_SCHEMA)])
    df = df.sort(["t_ms", "seq"], maintain_order=True).with_columns(
        pl.int_range(pl.len()).cast(df["seq"].dtype).alias("seq")
    )
    store.write_pull(pull, df, events_hash(df), store.truncated(pull.pull_key), store.positions(pull.pull_key))
