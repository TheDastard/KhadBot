"""
tests/fixtures/encounter/wcl/build_synth.py

Deterministic generator for the SYNTH01 raw-page fixture. Every event, actor and
spell ID here is invented (spell IDs 900001+); nothing comes from a real log.

Run from the repo root:  uv run python tests/fixtures/encounter/wcl/build_synth.py
The output (tests/fixtures/encounter/wcl/SYNTH01/) is committed; this script is kept so the
fixture can be regenerated and reviewed.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

OUT = Path(__file__).parent / "SYNTH01"
CODE = "SYNTH01"

# --- actors -----------------------------------------------------------------
TANK, HEALER, DPS1, DPS2, DPS3 = 1, 2, 3, 4, 5
PET = 6  # owned by DPS1
BOSS, ADD = 10, 11
PLAYERS = [TANK, HEALER, DPS1, DPS2, DPS3]

ACTORS = [
    {"id": TANK, "name": "Tankyboi", "type": "Player", "subType": "Warrior", "petOwner": None, "gameID": None},
    {"id": HEALER, "name": "Healz", "type": "Player", "subType": "Priest", "petOwner": None, "gameID": None},
    {"id": DPS1, "name": "Alphadps", "type": "Player", "subType": "Hunter", "petOwner": None, "gameID": None},
    {"id": DPS2, "name": "Betadps", "type": "Player", "subType": "Rogue", "petOwner": None, "gameID": None},
    {"id": DPS3, "name": "Gammadps", "type": "Player", "subType": "Mage", "petOwner": None, "gameID": None},
    {"id": PET, "name": "Fido", "type": "Pet", "subType": "Pet", "petOwner": DPS1, "gameID": 900800},
    {"id": BOSS, "name": "Synthetic Boss", "type": "NPC", "subType": "Boss", "petOwner": None, "gameID": 900900},
    {"id": ADD, "name": "Synthetic Add", "type": "NPC", "subType": "NPC", "petOwner": None, "gameID": 900901},
]

# --- abilities (all fake) -----------------------------------------------------
SWIRL, FIREPOOL, CATACLYSM, SHADOWBOLT, PHASE_SHIFT, CURSE = 900101, 900102, 900201, 900401, 900501, 900601
LINK_TOTEM, EXTERNAL = 900301, 900302
LINK_TOTEM_AURA = 900311  # the aura has its own ID, like Spirit Link Totem's
BATTLE_CRY, FURY = 902001, 902002  # offensive cooldowns: raid and personal
BATTLE_CRY_AURA = 902011
KICK, PURGE = 900701, 900702
FILLERS = [900001, 900002, 900003]
BOSS_MELEE = 900010
TAUNT = 900703

ENEMY_CAST_FILTER_IDS = [SWIRL, CATACLYSM, SHADOWBOLT, PHASE_SHIFT, CURSE]
BUFF_FILTER_IDS = [LINK_TOTEM, EXTERNAL, LINK_TOTEM_AURA]
OFFENSIVE_BUFF_FILTER_IDS = [BATTLE_CRY, FURY, BATTLE_CRY_AURA]

ABILITIES = [
    {"gameID": i, "name": n, "type": 1}
    for i, n in [
        (SWIRL, "Synth Swirl"),
        (FIREPOOL, "Synth Fire Pool"),
        (CATACLYSM, "Synth Cataclysm"),
        (SHADOWBOLT, "Synth Shadow Bolt"),
        (PHASE_SHIFT, "Synth Phase Shift"),
        (CURSE, "Synth Curse"),
        (LINK_TOTEM, "Synth Link Totem"),
        (LINK_TOTEM_AURA, "Synth Link Totem"),
        (EXTERNAL, "Synth Guardian"),
        (BATTLE_CRY, "Synth Battle Cry"),
        (BATTLE_CRY_AURA, "Synth Battle Cry"),
        (FURY, "Synth Fury"),
        (KICK, "Synth Kick"),
        (PURGE, "Synth Purge"),
        (BOSS_MELEE, "Synth Melee"),
        (TAUNT, "Synth Taunt"),
        (900001, "Synth Strike A"),
        (900002, "Synth Strike B"),
        (900003, "Synth Strike C"),
    ]
]

FIGHTS = [
    # Wipe: 60 s, reaches phase 2, boss at 35%.
    {"id": 1, "kill": False, "start": 100_000, "end": 160_000, "pct": 35.0, "hp_pct": 41.0, "last_phase": 2},
    # Kill: 80 s.
    {"id": 2, "kill": True, "start": 300_000, "end": 380_000, "pct": 0.0, "hp_pct": 0.0, "last_phase": 2},
]

MAX_HP = {TANK: 200_000, HEALER: 100_000, DPS1: 100_000, DPS2: 100_000, DPS3: 100_000}


def fhash(expr: str | None) -> str:
    return "none" if not expr else hashlib.sha256(expr.encode()).hexdigest()[:12]


def ids_filter(ids: list[int]) -> str:
    return "ability.id IN (" + ",".join(str(i) for i in sorted(set(ids))) + ")"


def _sort_key(e: dict) -> tuple:
    return (e["timestamp"], e["type"], e["sourceID"], e.get("targetID", -1))


class Sim:
    """
    Tiny HP simulation so damage events carry plausible hitPoints and overkill.

    ``hit`` / ``heal`` only queue operations; ``run`` applies them in time order (stable for equal
    timestamps), so HP follows the fight's timeline regardless of the order the script lists them.
    """

    def __init__(self, start: int) -> None:
        self.start = start
        self.hp = dict(MAX_HP)
        self.dead: set[int] = set()
        self.damage: list[dict] = []
        self.deaths: list[dict] = []
        self.healing: list[dict] = []
        self._ops: list[tuple[int, int, str, tuple]] = []

    def hit(self, t: int, src: int, tgt: int, ability: int, amount: int) -> None:
        self._ops.append((t, len(self._ops), "hit", (src, tgt, ability, amount)))

    def heal(self, t: int, tgt: int, amount: int, ability: int = 900003) -> None:
        self._ops.append((t, len(self._ops), "heal", (tgt, amount, ability)))

    def run(self) -> None:
        for t, _, kind, args in sorted(self._ops):
            if kind == "hit":
                self._apply_hit(t, *args)
            else:
                self._apply_heal(t, *args)
        self._ops.clear()

    def _apply_hit(self, t: int, src: int, tgt: int, ability: int, amount: int) -> None:
        if tgt in self.dead or tgt not in self.hp:
            return
        before = self.hp[tgt]
        after = max(0, before - amount)
        overkill = max(0, amount - before)
        self.hp[tgt] = after
        self.damage.append(
            {
                "timestamp": self.start + t,
                "type": "damage",
                "sourceID": src,
                "targetID": tgt,
                "abilityGameID": ability,
                "fight": 0,
                "hitType": 1,
                "amount": amount - overkill,
                "mitigated": 0,
                "unmitigatedAmount": amount,
                "absorbed": 0,
                "overkill": overkill,
                "hitPoints": after,
                "maxHitPoints": MAX_HP[tgt],
            }
        )
        if after == 0:
            self.dead.add(tgt)
            self.deaths.append(
                {
                    "timestamp": self.start + t,
                    "type": "death",
                    # As in real reports: no source or ability on the row itself, the killing blow beside it.
                    "sourceID": -1,
                    "targetID": tgt,
                    "abilityGameID": 0,
                    "fight": 0,
                    "killerID": src,
                    "killingAbilityGameID": ability,
                }
            )

    def _apply_heal(self, t: int, tgt: int, amount: int, ability: int) -> None:
        if tgt in self.dead:
            return
        before = self.hp[tgt]
        after = min(MAX_HP[tgt], before + amount)
        self.hp[tgt] = after
        self.healing.append(
            {
                "timestamp": self.start + t,
                "type": "heal",
                "sourceID": HEALER,
                "targetID": tgt,
                "abilityGameID": ability,
                "fight": 0,
                "amount": after - before,
                "overheal": amount - (after - before),
                "hitPoints": after,
                "maxHitPoints": MAX_HP[tgt],
            }
        )


def build_fight(fight: dict) -> dict[str, list[dict]]:
    """Return {spec key: events} for one fight. Everything is scripted; no randomness."""
    s = fight["start"]
    sim = Sim(s)
    kill = fight["kill"]
    dur = fight["end"] - fight["start"]
    casts: list[dict] = []
    enemy_casts: list[dict] = []
    buffs: list[dict] = []
    offensive_buffs: list[dict] = []
    debuffs: list[dict] = []
    interrupts: list[dict] = []
    dispels: list[dict] = []
    threat: list[dict] = []

    def ev(t: int, typ: str, src: int, tgt: int | None, ability: int, **kw: int) -> dict:
        out = {"timestamp": s + t, "type": typ, "sourceID": src, "abilityGameID": ability, "fight": 0}
        if tgt is not None:
            out["targetID"] = tgt
        out.update(kw)
        return out

    # Boss melee on the tank every 2 s; healer tops people up on a fixed cadence.
    for t in range(1000, dur, 2000):
        sim.hit(t, BOSS, TANK, BOSS_MELEE, 6_000)
        threat.append(ev(t, "cast", BOSS, TANK, BOSS_MELEE, melee=True))
    # Threat data: a swing that missed (no damage row), and DPS1 taunting the boss for one swing. The
    # taunt cast is also in Casts and Threat repeats a death, as WCL does; normalize drops both copies.
    threat.append(ev(2_000, "cast", BOSS, TANK, BOSS_MELEE, melee=True))
    casts.append(ev(9_400, "cast", DPS1, BOSS, TAUNT))
    threat.append(ev(9_400, "cast", DPS1, BOSS, TAUNT))
    threat.append(ev(9_400, "applydebuff", DPS1, BOSS, TAUNT))
    threat.append(ev(10_000, "cast", BOSS, DPS1, BOSS_MELEE, melee=True))
    threat.append(ev(dur - 1, "death", 0, DPS2, 0))
    threat.append(ev(30_000, "death", 0, ADD, 0, targetInstance=1))  # NPC deaths exist only in Threat
    for t in range(2000, dur, 3000):
        sim.heal(t, TANK, 12_000)
        if t % 6000 == 2000:
            for p in (HEALER, DPS1, DPS2, DPS3):
                sim.heal(t + 100, p, 15_000)

    # Swirl (avoidable): a scripted target every 5 s.
    swirl_targets = [
        DPS1,
        DPS2,
        DPS3,
        HEALER,
        DPS2,
        DPS3,
        DPS1,
        DPS2,
        DPS3,
        HEALER,
        DPS3,
        DPS2,
        DPS1,
        DPS3,
        DPS2,
        HEALER,
    ]
    for i, t in enumerate(range(5000, dur - 1000, 5000)):
        enemy_casts.append(ev(t - 1500, "begincast", BOSS, None, SWIRL))
        sim.hit(t, BOSS, swirl_targets[i % len(swirl_targets)], SWIRL, 14_000)

    if not kill:
        # One-shot: DPS1 is topped up at 7.9 s, then takes a 140k hit at 8 s from full health.
        sim.heal(7_900, DPS1, 15_000)
        sim.hit(8_000, BOSS, DPS1, SWIRL, 140_000)
        # Multi-hit death: DPS3 stands in Fire Pools 21-24 s (unregistered ability).
        for t in (21_000, 22_000, 23_000, 24_000):
            sim.hit(t, BOSS, DPS3, FIREPOOL, 28_000)
        # Cataclysm raid damage at 20 s (covered by Link Totem) and 40 s (uncovered).
        for t in (20_000, 40_000):
            enemy_casts.append(ev(t - 2000, "begincast", BOSS, None, CATACLYSM))
            enemy_casts.append(ev(t - 1, "cast", BOSS, None, CATACLYSM))
            for p in PLAYERS:
                sim.hit(t, BOSS, p, CATACLYSM, 35_000 if t == 20_000 else 62_000)
        # Third death (the 5-player cutoff) at 41 s, a fourth after it at 50 s.
        sim.hit(41_000, BOSS, DPS2, FIREPOOL, 90_000)
        sim.hit(50_000, BOSS, TANK, BOSS_MELEE, 400_000)
        # Interrupts: first Shadow Bolt interrupted, second one lands.
        enemy_casts.append(ev(10_000, "begincast", ADD, None, SHADOWBOLT))
        interrupts.append(ev(11_970, "interrupt", DPS2, ADD, KICK, extraAbilityGameID=SHADOWBOLT))
        enemy_casts.append(ev(30_000, "begincast", ADD, None, SHADOWBOLT))
        enemy_casts.append(ev(32_000, "cast", ADD, None, SHADOWBOLT))
        sim.hit(32_001, ADD, HEALER, SHADOWBOLT, 12_000)
        # Phase 2 trigger.
        enemy_casts.append(ev(35_000, "cast", BOSS, None, PHASE_SHIFT))
        # Cooldowns: Link Totem at 18 s (8 s aura), an external at 38 s.
        casts.append(ev(18_000, "cast", HEALER, None, LINK_TOTEM))
        casts.append(ev(38_000, "cast", DPS3, TANK, EXTERNAL))
        for p in PLAYERS:
            buffs.append(ev(18_050, "applybuff", HEALER, p, LINK_TOTEM_AURA))
            buffs.append(ev(26_050, "removebuff", HEALER, p, LINK_TOTEM_AURA))
        buffs.append(ev(38_050, "applybuff", DPS3, TANK, EXTERNAL))
        buffs.append(ev(44_050, "removebuff", DPS3, TANK, EXTERNAL))
        # Curse on the tank lasts 16 s, dispelled late.
        debuffs.append(ev(14_000, "applydebuff", BOSS, TANK, CURSE))
        debuffs.append(ev(30_000, "removedebuff", BOSS, TANK, CURSE))
        dispels.append(ev(29_950, "dispel", HEALER, TANK, PURGE, extraAbilityGameID=CURSE))
    else:
        for t in (20_000, 45_000):
            enemy_casts.append(ev(t - 2000, "begincast", BOSS, None, CATACLYSM))
            enemy_casts.append(ev(t - 1, "cast", BOSS, None, CATACLYSM))
            for p in PLAYERS:
                sim.hit(t, BOSS, p, CATACLYSM, 20_000)
        for t in (12_000, 36_000, 60_000):
            enemy_casts.append(ev(t - 2000, "begincast", ADD, None, SHADOWBOLT))
            interrupts.append(ev(t - 30, "interrupt", DPS2, ADD, KICK, extraAbilityGameID=SHADOWBOLT))
        enemy_casts.append(ev(30_000, "cast", BOSS, None, PHASE_SHIFT))
        casts.append(ev(19_000, "cast", HEALER, None, LINK_TOTEM))
        casts.append(ev(44_000, "cast", HEALER, None, LINK_TOTEM))
        for t0 in (19_050, 44_050):
            for p in PLAYERS:
                buffs.append(ev(t0, "applybuff", HEALER, p, LINK_TOTEM_AURA))
                buffs.append(ev(t0 + 8_000, "removebuff", HEALER, p, LINK_TOTEM_AURA))
        debuffs.append(ev(14_000, "applydebuff", BOSS, TANK, CURSE))
        debuffs.append(ev(19_000, "removedebuff", BOSS, TANK, CURSE))
        dispels.append(ev(18_900, "dispel", HEALER, TANK, PURGE, extraAbilityGameID=CURSE))
        sim.hit(55_000, BOSS, DPS3, FIREPOOL, 150_000)  # one death in the kill

    # Offensive cooldowns: Battle Cry buffs the raid for 40 s from 3 s, through the first Cataclysm and the
    # first death, which must still count no defensive. In the wipe, the Mage also uses Fury at 5 s.
    casts.append(ev(3_000, "cast", HEALER, None, BATTLE_CRY))
    for p in PLAYERS:
        offensive_buffs.append(ev(3_050, "applybuff", HEALER, p, BATTLE_CRY_AURA))
        offensive_buffs.append(ev(43_050, "removebuff", HEALER, p, BATTLE_CRY_AURA))
    if not kill:
        casts.append(ev(5_000, "cast", DPS3, None, FURY))
        offensive_buffs.append(ev(5_050, "applybuff", DPS3, DPS3, FURY))
        offensive_buffs.append(ev(25_050, "removebuff", DPS3, DPS3, FURY))

    sim.run()

    # Friendly filler casts so the Casts type has volume.
    for i, t in enumerate(range(1500, dur, 3000)):
        for j, p in enumerate(PLAYERS):
            casts.append(ev(t + 100 * j, "cast", p, None, FILLERS[(i + j) % 3]))
    casts.append(ev(1_000, "cast", PET, None, FILLERS[0]))

    return {
        "Deaths": sorted(sim.deaths, key=_sort_key),
        "DamageTaken": sorted(sim.damage, key=_sort_key),
        "Casts": sorted(casts, key=_sort_key),
        "Casts-Enemies": sorted(enemy_casts, key=_sort_key),
        "Buffs": sorted(buffs, key=_sort_key),
        "Buffs:offensive": sorted(offensive_buffs, key=_sort_key),
        "Debuffs": sorted(debuffs, key=_sort_key),
        "Interrupts": sorted(interrupts, key=_sort_key),
        "Dispels": sorted(dispels, key=_sort_key),
        "Healing": sorted(sim.healing, key=_sort_key),
        "Threat": sorted(threat, key=_sort_key),
    }


FILTERS = {
    "Buffs": ids_filter(BUFF_FILTER_IDS),
    "Buffs:offensive": ids_filter(OFFENSIVE_BUFF_FILTER_IDS),
    "Casts-Enemies": ids_filter(ENEMY_CAST_FILTER_IDS),
}


def write_pages(directory: Path, events: list[dict], *, split: bool) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    if split and len(events) > 1:
        mid = len(events) // 2
        chunks = [events[:mid], events[mid:]]
    else:
        chunks = [events]
    for i, chunk in enumerate(chunks):
        nxt = chunks[i + 1][0]["timestamp"] if i + 1 < len(chunks) else None
        (directory / f"page_{i:03d}.json").write_text(
            json.dumps({"data": chunk, "nextPageTimestamp": nxt}, indent=1) + "\n", encoding="utf-8"
        )
    (directory / "_complete").write_text("", encoding="utf-8")


def main() -> None:
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)

    fights_meta = []
    for f in FIGHTS:
        fights_meta.append(
            {
                "id": f["id"],
                "encounterID": 90001,
                "name": "Synthetic Boss",
                "difficulty": 5,
                "kill": f["kill"],
                "startTime": f["start"],
                "endTime": f["end"],
                "lastPhase": f["last_phase"],
                "fightPercentage": f["pct"],
                "bossPercentage": f["hp_pct"],
                "size": 5,
                "friendlyPlayers": PLAYERS,
                "phaseTransitions": [{"id": 1, "startTime": f["start"]}, {"id": 2, "startTime": f["start"] + 35_000}],
            }
        )
    report = {
        "title": "Synthetic Test Report",
        "startTime": 1_700_000_000_000,
        "zone": {"id": 9000, "name": "Synthetic Citadel"},
        "masterData": {"actors": ACTORS, "abilities": ABILITIES},
        "fights": fights_meta,
        "phases": [
            {
                "encounterID": 90001,
                "phases": [
                    {"id": 1, "name": "Synth Stage One", "isIntermission": False},
                    {"id": 2, "name": "Synth Stage Two", "isIntermission": False},
                ],
            }
        ],
    }
    (OUT / "report.json").write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")

    for f in FIGHTS:
        for key, events in build_fight(f).items():
            filt = FILTERS.get(key)
            split = key == "DamageTaken" and f["id"] == 1  # the two-page data type
            # "Buffs:offensive" is a second Buffs request: same directory, its own filter hash.
            write_pages(OUT / str(f["id"]) / key.partition(":")[0] / fhash(filt), events, split=split)


if __name__ == "__main__":
    main()
