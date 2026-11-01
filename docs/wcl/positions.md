# WarcraftLogs API: instances and positions

## Instances and positions

- **`sourceInstance` / `targetInstance`** number NPCs that share one actor ID (both Weakened Doomscales are one
  actor; Blightscale Rawlings 51 to 54 are one actor). They appear on every data type and are null for unique
  actors and players. KhadBot keeps them as `source_instance` / `target_instance`.
- **Positions**: events with `includeResources` carry `x`, `y`, `facing` and `mapID` for one unit,
  `resourceActor` saying which (1 source, 2 target). 73% of rows in the 62 recorded pulls have one. They cost
  nothing extra to fetch (already in the raw pages). Measured over the 62 pulls (2026-10-11, scratch spike, not
  in the codebase): parquet 54.0 MB -> 75.0 MB (+39%); normalize 20.1 s -> 26.1 s in total (about 2 s per
  night); reading a pull 20 -> 22 ms median; an as-of query for the boss-to-target distance at every swing about
  8 ms per pull. What the positions are good for is in **Position data** below.
- **NPCs have no spawn event.** No fetched data type records an NPC appearing. Its first event (a cast, a melee
  swing, damage, or a player's cast aimed at it) is the earliest sign of it, so its spawn time is unknown, and
  its first position comes from that event, after it may have moved. Something a player carries as a debuff (an
  egg on Ula'tek) isn't an actor at all; only the debuff shows it.
- **Some NPCs appear only as targets.** An NPC that deals no damage and never swings shows up only in its own
  casts and in players' casts aimed at it, with no death in the Threat data (the Venomous Heart on Ula'tek).

## Position data (2026-10-16)

Measured on the 47 Heroic Ula'tek pulls from the raw pages in `KHADBOT_DATA_DIR` (scratch scripts, not in the
codebase; no API calls). The decision that follows from it is in `docs/decisions.md`.

### What carries a position

2.15 M samples. Every one is on map 2610, and only these data types carry positions:

| Data type | Samples | Whose position |
|---|---|---|
| Healing | 1.52 M | mostly the target player (`resourceActor` 2), sometimes the healer; pets too |
| DamageTaken | 410 k | the player hit (`resourceActor` 2) |
| Casts (friendly) | 212 k | the caster |
| Threat | 7.3 k | the NPC swinging (`resourceActor` 1) |
| Casts (enemy) | 2.3 k | the NPC casting |

Buffs, Debuffs and Deaths carry none. NPC positions come only from their own swings and casts, because
KhadBot doesn't fetch damage done to NPCs. So Ula'tek has a sample about every 8 s, and Gore Rattle (259555)
about every 15 s. Players have about 7 per second.

- **A hit carries its own position.** 97% of the mechanic damage rows looked at below carry the hit player's
  position on that row, so whoever is hit has an exact position at that moment. An NPC's own cast row carries
  its position at the cast.
- **The bosses stay on fixed spots.** Ula'tek is only ever seen at 7 spots: one in Stage One, two in Stage
  Two and four in Stage Three. Gore Rattle is seen at 4 (two from 8 to 62 s, two from 73 to 128 s). The
  Spectral Coil NPCs use 8. A position between two of these spots is never recorded.
- `facing` runs from -786 to -158 (a span of 628, so radians × 100 with an offset). Nothing below needed it.

### Scale: 1 yard = 100 units

Player speed between consecutive samples 0.4 to 1.5 s apart (128 k segments) peaks at 700 to 750 units/s,
the 7 yards/s base run speed. Smaller peaks at about 1,000 and 1,450 units/s are speed boosts. Every radius
below agrees with that scale.

| Anchor | Distance in units |
|---|---|
| Ula'tek to the target of her melee swings (n=932, target sample ≤ 1 s old) | 1,336 to 2,043 (p5 to p95), median 1,609, max 3,028 |
| Gore Rattle to the target of its melee swings (n=399) | 810 to 1,714 (p5 to p95), median 1,106, max 3,066 |
| Circling Prey hits over 1M before mitigation (n=5), from Ula'tek at her cast | 1,381 to 2,447 |
| Circling Prey hits under 1M (n=228) | 3,057 to 8,203 |
| Volatile Purge: another erupter within this distance of each player hit 2 or 3 times (n=17) | 135 to 515 |
| Volatile Purge: nearest other erupter for erupters hit once (n=244) | 448 and up |
| Serpent's Bite: nearest bitten player, players given Ingested Venom (n=300) | 23 to 660 (p0 to p95) |
| Serpent's Bite: nearest bitten player, players not given it (n=45) | 373 to 3,763, p5 752 |
| Spectral Coils: Soul Constrictor recipients, from that coil's cast position (n=2,293) | all but 1 within 3,000 |

The Circling Prey split is a clean gap with no overlap. It is measured from Ula'tek's centre, and her size is
not known, so it can't confirm the 13-yard radius itself.

### Freshness

The age of each living player's latest sample at 501 mechanic moments (the casts of Unchecked Rage, Rattler Slam,
Mephitic Thrash, Circling Prey, Serpent's Bite and Spectral Coil, and Volatile Purge debuff removals). Roles per
pull: tank = 10 or more swings taken from Ula'tek or Gore Rattle; melee = median distance to Ula'tek's current
spot under 2,500; ranged = the rest.

| Role | Player-moments | p50 | p90 | p95 | p99 | Over 1 s |
|---|---|---|---|---|---|---|
| Tank | 897 | 80 ms | 294 ms | 399 ms | 697 ms | 0.2% |
| Melee | 1,061 | 124 ms | 431 ms | 551 ms | 818 ms | 0.2% |
| Ranged | 7,322 | 104 ms | 375 ms | 502 ms | 813 ms | 0.5% |
| Ula'tek (boss) | 501 | 2.4 s | 23.9 s | 28.7 s | 31.7 s | |

- At 7 yards/s, a 0.5 s old sample is up to 3.5 yards out for a player who is running. That is close to the
  5- and 7-yard radii below, so a rule should prefer the hit row's own position and treat an older sample as
  uncertain.
- Ula'tek's stale samples matter less than they look, because her position only changes from one fixed spot to
  another. Across 74 spot changes, the gap between the last sample at the old spot and the first at the new one is 6.2 s at the
  median and 58 s at most. Her own cast rows always carry her exact position.

### Which questions positions answer

| Question | Answer | Evidence |
|---|---|---|
| Was the aggro holder in melee range at Unchecked Rage or Rattler Slam? | **Yes.** | Raid-wide hits, so the holder's position is on their own hit row (age 0 to 500 ms). Of 21 Unchecked Rages, 9 had a living holder 3,092 to 5,546 units away, beyond all 932 of Ula'tek's swings. 10 had a holder who had already died: 8 in one wipe (`H7z1fqDXjRTFnbtg:10`, holder dead 1.7 s before the first) and 2 within 0.11 s of the death. 2 had no holder. Of 10 Rattler Slams, 6 had a living holder 2,106 to 5,187 away (Gore Rattle's swings: p95 1,714), 1 had a holder dead 1.6 s earlier, and 3 had no holder. Every holder in range was dead, so death and distance together separate "tank out of position", "tank died" and "nobody had aggro". |
| Who stood inside Mephitic Thrash's 35 yards? | **Measurable, not verifiable.** | All 22 players are hit at every impact for the same amount (unmitigated median 401,660 outside 3,500 units, 416,240 inside), so nothing in the damage confirms the radius or the sweep's shape. Of 1,922 impacts, 74 were within 3,500 of Gore Rattle: 59 on the tail's own aggro holder, who has to stand there, and 15 on other players over 13 casts. |
| Who stood inside Circling Prey's 13 yards? | **Yes, but the damage already says so.** | The over-1M hits sit 1,381 to 2,447 from Ula'tek and every other hit is over 3,057 away. The existing `hit_amount_gt` rule finds the same players. |
| Who failed to spread for Volatile Purge? | **Yes; the radius is about 5 yards.** | 21 eruptions, 10 to 19 erupters each. Every player hit 2 or 3 times had another erupter within 515 units; no erupter hit once had one closer than 448. The existing `hits_per_cluster_gt 1` rule finds the failures from damage; positions add whose eruption they stood in. |
| Who stood in a soak? | **Yes, for both soaks.** | Serpent's Bite (27 bites, mostly 3 bitten at once): 291 of 300 players given Ingested Venom stood within 700 units (7 yards) of a bitten player, against 2 of 45 not given it. Spectral Coils: unmitigated damage is the same for everyone at an impact, but Soul Constrictor from a coil lands on the players within about 3,000 units (30 yards) of that coil: 2,292 of 2,293 recipients were inside, and 83 of 3,089 non-recipients were within 2,750. |

### Shape and cost

| Form | Extra storage on the 47 pulls (events: 2.97 M rows, 38.7 MB) |
|---|---|
| Columns on every event row (spike of 2026-10-11, 62 pulls) | +39% |
| Separate per-pull table `t_ms, actor_id, instance, x, y` (2.02 M rows) | +11.5 MB (+30%) |
| The same, keeping only rows where the position changed | +7.1 MB (+18%), but loses "still there at t" |

A per-pull table reads in about 7 ms. An as-of lookup of 1,000 (actor, time) probes takes 1.5 to 3.3 ms after the
first call. Normalize already parses every page, so the extra work is about the 2 s per night the columns
spike measured.
