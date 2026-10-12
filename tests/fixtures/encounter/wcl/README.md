# Synthetic WCL fixture (SYNTH01)

**This data is synthetic.** Nothing here comes from a real WarcraftLogs report. All
spell IDs are fake (900001 and up) and all actor names are invented. It exists so the
ingest, normalize, store and (later) annotation code can be tested offline.

Regenerate with `uv run python tests/fixtures/encounter/wcl/build_synth.py`. The generator is the
source of truth; the committed `SYNTH01/` tree is its output.

## Layout

Raw page cache layout (plan section 5.2), rooted here instead of `$KHADBOT_DATA_DIR/raw`:

    SYNTH01/report.json                              # GraphQL `reportData.report` object
    SYNTH01/<fight>/<DataType>[-Enemies]/<filter_hash>/page_NNN.json
    SYNTH01/<fight>/<DataType>[-Enemies]/<filter_hash>/_complete

`Enemies` hostility gets a `-Enemies` directory suffix so friendly and enemy `Casts` never
share a directory. Filter hashes: `Buffs` uses `ability.id IN (900301,900302)`; enemy
`Casts` uses `ability.id IN (900101,900201,900401,900501,900601)`; everything else is `none`.

## Pulls

| Fight | Outcome | Length | Purpose |
|-------|---------|--------|---------|
| 1 | wipe, fight 35% (boss HP 41%), phase 2 | 60 s | deaths, cutoff, missed interrupt, covered + uncovered window, unverified cooldown use |
| 2 | kill | 80 s | clean-ish kill; both Cataclysms covered; every interrupt lands |

## What each event exists to test

- **One-shot death** (fight 1, t=8 s): the player is healed to full at 7.9 s, then a 140k Swirl hit on a
  100k-HP player (`overkill` > 0, time-to-die ~0.1 s).
- **Multi-hit death** (fight 1, t=23 s): after Cataclysm, Fire Pool (ability 900102, deliberately *not* in
  the encounter file, so it shows up as unregistered damage) hits a player at 21, 22 and 23 s.
- **Deaths at the wipe cutoff / after it** (fight 1, t=41 s and t=50 s): with 5 players the
  cutoff is the 3rd death (Fire Pool at 41 s); the tank's death at 50 s is after it.
- **Kill with a death** (fight 2, t=55 s): Swirl then Fire Pool on the same player.
- **`any_hit` mechanic**: Swirl (900101) hits one player every 5 s.
- **Interrupt mechanic**: Shadow Bolt (900401) is interrupted at ~12 s in fight 1 and lands
  at 32 s (missed interrupt). Fight 2 interrupts all three casts.
- **Damage window**: Cataclysm (900201) at 20 s is covered by Link Totem (900301, cast at
  18 s, buff 18-26 s); the one at 40 s is not.
- **Cooldown casts**: Link Totem and Guardian (900302) in fight 1; Link Totem x2 in fight 2.
- **Phase trigger**: Boss cast of Phase Shift (900501) at 35 s (fight 1) and 30 s (fight 2).
- **hitPoints / maxHitPoints** on damage and heal events (target's HP after the event). The generator
  queues damage and heals and applies them in time order, so HP follows the fight's timeline.
- **Pagination**: fight 1 `DamageTaken` is two pages (`nextPageTimestamp` set on page 0).
- **Threat**: the boss swings at the tank every 2 s, one extra swing misses (no damage row), and Alphadps
  taunts the boss at 9.4 s and takes its next swing. As in real reports, the taunt cast is also in `Casts`
  and Threat repeats a player's death; normalize drops both copies. Threat also has the add's death at
  30 s, which no other data type has, and normalize keeps it.
- **Debuff duration / dispel**: Curse (900601) on the tank lasts 16 s in fight 1 (dispelled
  late) and 5 s in fight 2.
- **Pets**: actor 6 is owned by actor 3 and casts once, to test the friendly flag for pets.

# Real WCL fixtures

Seven pulls recorded on 2026-10-11 from three public reports of one guild's Heroic Ula'tek progression
(encounter 3492, The Venomous Abyss). The full reports live only in `$KHADBOT_DATA_DIR`; these pulls were
copied from that raw cache. API notes and costs: `docs/wcl-api.md`.

Each report directory has the full `report.json` (every fight), but only the listed fights have pages, so
offline ingest must pass `fight_ids`. `bossPercentage` was merged into each `report.json` afterwards
(2026-10-11) from a live `fights { id bossPercentage }` query, when the report query started asking for it. Everything under a real report directory is stored in Git LFS (see
`.gitattributes` here); run `git lfs pull` if the pages are pointer files. Pre-commit hooks skip these
directories so the recorded bytes are never rewritten. Filters: `Buffs` uses the 17 cooldown-registry IDs from
`config/registries/cooldowns.yaml`. Enemy `Casts` is recorded twice for Ula'tek pulls: unfiltered (`none`) and
filtered to the encounter file's ability IDs, which is what an ingest with the current config fetches.
`Threat` (Friendlies side) and everything else are unfiltered (`none`).

| Pull | Outcome | Length | Why it is here |
|---|---|---|---|
| `H7z1fqDXjRTFnbtg:20` | Ula'tek kill, all 4 phases | 579 s | The clean kill. 11 deaths on a kill, one player dying twice (battle res). |
| `H7z1fqDXjRTFnbtg:15` | Ula'tek wipe at the enrage, Stage Three, 5.6% fight progress | 618 s | Failed Stage Three by attrition: 255 s in Stage Three, deaths in waves, ending in *Fury Unleashed* (the enrage). No enemy casts are recorded after 574 s. |
| `H7z1fqDXjRTFnbtg:10` | Ula'tek wipe, phase 1, boss 95% | 37 s | Early reset; 21-player raid. |
| `H7z1fqDXjRTFnbtg:23` | Mythic Nymrissa wipe, boss 78% | 123 s | Different encounter and difficulty; no phase transitions; WCL `size=20` with 22 players. |
| `trMgApZCVYkv3jdq:11` | Ula'tek wipe, phase 2, boss 53% | 244 s | Typical wipe from the wipe-heavy night; one player dying twice. |
| `trMgApZCVYkv3jdq:18` | Ula'tek wipe, 35 s into Stage Three (phase ID 4) | 398 s | No deaths before Stage Three, then 20 deaths in 3 s around *Unchecked Rage*: a collapse at the start of a stage. |
| `QkVa6GHRK8yfrTbw:14` | Ula'tek wipe, phase 1, boss 80% | 106 s | Early-learning wipe from the first night. |
