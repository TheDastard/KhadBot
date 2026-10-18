"""
scripts/build_cooldown_registry.py

Propose cooldown-registry entries from SimulationCraft's generated client data.

    uv run python scripts/build_cooldown_registry.py --fetch midnight
    uv run python scripts/build_cooldown_registry.py --simc-dir path/to/engine/dbc/generated

--fetch downloads the tables from the SimC repository at that branch, tag or commit, pinned to the
commit it resolves to, into $KHADBOT_DATA_DIR/simc/<commit>/ (once per commit). --simc-dir reads a
copy that is already on disk.

The script never edits config/registries/cooldowns.yaml. It writes a review file (default
$KHADBOT_DATA_DIR/registry/cooldowns.candidates.yaml) with three sections:

    candidates      every player cooldown that helps the raid live, with kind, role, cooldown,
                    duration, the aura spells Buffs events will show, and the effects that put it there
    registry_check  each registry entry against the game data: matched, name_differs, kind_differs,
                    not_a_candidate or not_in_game_data, with the data's cooldown and duration
    scripted        long-cooldown spells whose effects are scripted, which the heuristic can't classify

A person copies what they accept into cooldowns.yaml, still with verified: false until checked in game.

It also writes a friendly-damage review file (default
$KHADBOT_DATA_DIR/registry/friendly_damage.candidates.yaml) for config/registries/friendly_damage.yaml,
which it never edits either:

    candidates      every spell with a class mask whose damage lands only on the caster or allies, with
                    its classes, in_registry, and class: null for a person to fill in
    registry_check  each registry entry against the game data, with the same statuses; kind_differs
                    here means the entry's origin is not class

Recorded logs remain the main source for that registry: the data misses scripted damage (Blessing of
Sacrifice), spells SimC leaves out (Spirit Link's damage) and items or borrowed powers.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

from khadbot.config import get_config
from khadbot.encounter.config_files import (
    DEFAULT_FRIENDLY_DAMAGE_PATH,
    DEFAULT_REGISTRY_PATH,
    load_friendly_damage,
    load_registry,
)
from khadbot.encounter.registry_candidates import (
    candidates,
    check_registry,
    friendly_damage_candidates,
    scripted_spells,
)
from khadbot.simc.client_data import ClientData, load_client_data
from khadbot.simc.fetch import fetch_tables


def _source(data: ClientData, commit: str | None) -> dict[str, Any]:
    return {"simc_commit": commit, "wow_build": data.build, "generated": date.today().isoformat()}


def build_report(data: ClientData, registry_path: Path, commit: str | None, min_cooldown_s: float) -> dict[str, Any]:
    registry = load_registry(registry_path)
    found = candidates(data, min_cooldown_s=min_cooldown_s)
    in_registry = {c.spell_id for c in registry.cooldowns}
    checks = check_registry(data, [(c.spell_id, c.name, c.kind) for c in registry.cooldowns], found)
    return {
        "source": {**_source(data, commit), "min_cooldown_s": min_cooldown_s},
        "candidates": [
            {
                "spell_id": c.spell_id,
                "name": c.name,
                "class": c.class_name,
                "kind": c.kind,
                "role": c.role,
                "cooldown_s": c.cooldown_s,
                "duration_s": c.duration_s,
                "charges": c.charges,
                "spec_ids": c.spec_ids,
                "aura_spell_ids": c.aura_spell_ids,
                "in_registry": c.spell_id in in_registry,
                "evidence": c.evidence,
            }
            for c in found
        ],
        "registry_check": [{k: v for k, v in vars(r).items() if v is not None} for r in checks],
        "scripted": [
            {"spell_id": s, "name": n, "class": cls}
            for s, n, cls in scripted_spells(data, min_cooldown_s=min_cooldown_s)
        ],
    }


def build_friendly_report(data: ClientData, registry_path: Path, commit: str | None) -> dict[str, Any]:
    registry = load_friendly_damage(registry_path)
    found = friendly_damage_candidates(data)
    in_registry = {a.ability_id for a in registry.abilities}
    checks = check_registry(data, [(a.ability_id, a.name, a.origin) for a in registry.abilities], found)
    return {
        "source": _source(data, commit),
        "candidates": [
            {
                "ability_id": c.spell_id,
                "name": c.name,
                "class": None,
                "origin": c.kind,
                "player_class": ", ".join(c.player_classes),
                "cooldown_s": c.cooldown_s,
                "duration_s": c.duration_s,
                "in_registry": c.spell_id in in_registry,
                "evidence": c.evidence,
            }
            for c in found
        ],
        "registry_check": [
            {"proposed_origin" if k == "proposed_kind" else k: v for k, v in vars(r).items() if v is not None}
            for r in checks
        ],
    }


def _check_lines(checks: list[dict[str, Any]], proposed_key: str) -> list[str]:
    lines = []
    for r in checks:
        detail = ""
        if r["status"] == "name_differs":
            detail = f" (game: {r['game_name']})"
        elif r["status"] == "kind_differs":
            detail = f" (proposed: {r[proposed_key]})"
        lines.append(f"  {r['spell_id']:>7} {r['registry_name']:<28} {r['status']}{detail}")
    return lines


def _friendly_summary(report: dict[str, Any]) -> str:
    cands = report["candidates"]
    lines = [
        f"{len(cands)} friendly-damage candidates, {sum(not c['in_registry'] for c in cands)} not in the registry yet",
        "friendly-damage registry check:",
        *_check_lines(report["registry_check"], "proposed_origin"),
    ]
    return "\n".join(lines)


def _summary(report: dict[str, Any]) -> str:
    cands = report["candidates"]
    lines = [
        f"WoW build {report['source']['wow_build']} (SimC {report['source']['simc_commit'] or 'local copy'})",
        f"{len(cands)} candidates: "
        + ", ".join(f"{n} {k}" for k, n in sorted(Counter(c["kind"] for c in cands).items()))
        + "; "
        + ", ".join(f"{n} {r}" for r, n in sorted(Counter(c["role"] for c in cands).items())),
        f"{sum(not c['in_registry'] for c in cands)} not in the registry yet",
        f"{len(report['scripted'])} scripted spells to look at by hand",
        "registry check:",
        *_check_lines(report["registry_check"], "proposed_kind"),
    ]
    return "\n".join(lines)


def _write(out: Path, header: str, report: dict[str, Any]) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(header + yaml.safe_dump(report, sort_keys=False, allow_unicode=True, width=120), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--fetch", metavar="REF", help="SimC branch, tag or commit to download (e.g. midnight)")
    source.add_argument("--simc-dir", type=Path, help="Directory holding SimC's engine/dbc/generated files")
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY_PATH)
    parser.add_argument("--friendly-damage", type=Path, default=DEFAULT_FRIENDLY_DAMAGE_PATH)
    parser.add_argument("--out", type=Path, help="Review file (default: $KHADBOT_DATA_DIR/registry/...)")
    parser.add_argument("--friendly-out", type=Path, help="Friendly-damage review file (default: beside --out)")
    parser.add_argument("--min-cooldown", type=float, default=60.0, help="Shortest cooldown to include, seconds")
    args = parser.parse_args()
    load_dotenv()
    data_dir = Path(get_config().encounter.data_dir)

    if args.fetch:
        simc_dir, commit = fetch_tables(
            args.fetch, data_dir / "simc", progress=lambda name: print(f"downloading {name}")
        )
    else:
        simc_dir, commit = args.simc_dir, None
    data = load_client_data(simc_dir)
    report = build_report(data, args.registry, commit, args.min_cooldown)
    friendly = build_friendly_report(data, args.friendly_damage, commit)

    out = args.out or data_dir / "registry" / "cooldowns.candidates.yaml"
    _write(
        out,
        "# Cooldown-registry candidates generated by scripts/build_cooldown_registry.py from SimC client data.\n"
        "# Review file only: nothing loads it. Copy accepted entries into config/registries/cooldowns.yaml\n"
        "# with verified: false until checked in game.\n",
        report,
    )
    friendly_out = args.friendly_out or out.with_name("friendly_damage.candidates.yaml")
    _write(
        friendly_out,
        "# Friendly-damage candidates generated by scripts/build_cooldown_registry.py from SimC client data.\n"
        "# Review file only: nothing loads it. Fill in class (redistribution, delayed_boss, self_inflicted or\n"
        "# involuntary) and copy accepted entries into config/registries/friendly_damage.yaml with\n"
        "# verified: false until a recorded log shows the damage. The data misses scripted damage, spells SimC\n"
        "# leaves out and item or borrowed-power effects, so the logs stay the main source.\n",
        friendly,
    )
    print(_summary(report))
    print(f"wrote {out}")
    print(_friendly_summary(friendly))
    print(f"wrote {friendly_out}")


if __name__ == "__main__":
    main()
