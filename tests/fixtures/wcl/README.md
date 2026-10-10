# Synthetic WCL fixture (SYNTH01)

**This data is synthetic.** Nothing here comes from a real WarcraftLogs report. All
spell IDs are fake (900001 and up) and all actor names are invented. It exists so the
ingest, normalize, store and (later) annotation code can be tested offline.

Regenerate with `uv run python tests/fixtures/wcl/build_synth.py`. The generator is the
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
| 1 | wipe, boss 35%, phase 2 | 60 s | deaths, cutoff, missed interrupt, covered + uncovered window, unverified cooldown use |
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
- **Debuff duration / dispel**: Curse (900601) on the tank lasts 16 s in fight 1 (dispelled
  late) and 5 s in fight 2.
- **Pets**: actor 6 is owned by actor 3 and casts once, to test the friendly flag for pets.
