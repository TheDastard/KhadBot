# WarcraftLogs API notes

Field meanings, quirks and measured costs of the WarcraftLogs v2 GraphQL API, as KhadBot uses it.
Every claim here comes from a live query or a recorded report. Add the report code and fight ID when you
record a new quirk, so it can be checked again.

Reference reports (public, Heroic Ula'tek progression, zone The Venomous Abyss):

| Code | Night | Content |
|---|---|---|
| `QkVa6GHRK8yfrTbw` | 9/29, learning | Nek'zali kill, 6 Coiled Altar pulls (kill on fight 11), 14 Ula'tek wipes, 4 trash fights |
| `trMgApZCVYkv3jdq` | 10/1, progression | 25 Ula'tek wipes, same 22 players every pull, 3 trash fights |
| `H7z1fqDXjRTFnbtg` | Final night | Nek'zali and Coiled Altar kills, 8 Ula'tek pulls (kill on fight 20), 6 Mythic Nymrissa wipes, 14 trash fights |

## Access

- Client-credentials token from `https://www.warcraftlogs.com/oauth/token`; queries go to `/api/v2/client`.
  Private reports need a user token and `/api/v2/user`.
- The app's budget is **3,600 points per hour** (`rateLimitData { limitPerHour pointsSpentThisHour pointsResetIn }`).

## Report metadata (`report.fights`, `report.masterData`)

All of these fields are confirmed on the live schema: `encounterID`, `name`, `difficulty`, `kill`, `startTime`,
`endTime`, `lastPhase`, `fightPercentage`, `bossPercentage`, `size`, `friendlyPlayers`, `phaseTransitions { id startTime }`.

| Field | Meaning and quirks |
|---|---|
| `encounterID` | Boss encounter ID; `0` for trash. Venomous Abyss: Nek'zali the Soulcoiler 3470, The Coiled Altar 3429, Ula'tek 3492, Nymrissa Wavecaller 3379. `fights(killType: Encounters)` drops trash. |
| `difficulty` | `4` Heroic, `5` Mythic. One report can mix difficulties (`H7z1fqDXjRTFnbtg` ends with Mythic pulls). |
| `startTime`, `endTime` | Milliseconds relative to the report start, not Unix time. |
| `fightPercentage` vs `bossPercentage` | Not the same. `bossPercentage` is the boss's health; `fightPercentage` is overall progress through the fight, so it is lower than boss health in later phases (`QkVa6GHRK8yfrTbw:20`: boss 38.62, fight 27.51). Both are about 0.01 on a kill. Keep both; use `fightPercentage` for "how close was the pull". |
| `lastPhase` | **The ordinal within its kind, not the phase ID.** Stages and intermissions are numbered separately, so a pull that ends in the first intermission reports `lastPhase=1` (`QkVa6GHRK8yfrTbw:19` ends in phase ID 3, "Intermission: The Shattering"). Ula'tek: phase IDs `[1,2]` report 2, `[1,2,3]` report 1 (intermission 1), `[1,2,3,4]` report 3 (Stage Three). Use the last `phaseTransitions` ID for the phase reached. |
| `phaseTransitions` | Phase IDs in order, with start times. Present on Heroic Ula'tek; empty on the Mythic Nymrissa pulls. |
| `report.phases` | Per encounter: `phases { id name isIntermission }` and `separatesWipes`. This is what maps phase IDs to names. `GET_REPORT_META` requests `phases { encounterID phases { id name isIntermission } }`, confirmed on 2026-10-17 by refreshing the three recorded reports: the names match the phase IDs below. |

### Phase IDs

| Encounter | ID 1 | ID 2 | ID 3 | ID 4 |
|---|---|---|---|---|
| Ula'tek (3492) | Stage One: Fury of the Serpent Mother | Stage Two: Children of the Doomscale | Intermission: The Shattering | Stage Three: Ula'tek's Ascension |
| The Coiled Altar (3429) | Stage One: Serpent's Bargain | Stage Two: Usurper's Reprisal | Intermission: The Claimed Vessel | Stage Three: Coiled Union |
| Nek'zali the Soulcoiler (3470) | Stage One: Soulcoiler Initiation | Intermission: Ritual of Awakening | Stage Two: Uncoiling | none |

Players count stages, not IDs: "phase 3" on Ula'tek means phase ID 4. On Heroic Ula'tek, Stage Three always started
362 to 365 s into the pull across the 14 pulls that reached it.
| `size` | **Not a reliable head count.** Mythic raids are locked to 20 except Lair fights, which allow 15 to 25. The `H7z1fqDXjRTFnbtg` Mythic Nymrissa pulls report `size=20`, yet 22 players took part. |
| `friendlyPlayers` | Player actor IDs for the pull, and accurate: in all 62 recorded pulls every listed player has events in that pull, and no other player does. Take each pull's roster and raid size from here, never from `size` or report-wide actors. |
| `masterData.actors(type: "Player")` | Every player seen anywhere in the report, not the raid. `H7z1fqDXjRTFnbtg` lists 276 for a 22-player raid. |

## Events (`report.events`)

- Arguments in use, all confirmed: `dataType` (`Deaths`, `DamageTaken`, `Casts`, `Buffs`, `Debuffs`, `Interrupts`,
  `Dispels`, `Healing`), `hostilityType` (`Friendlies`, `Enemies`), `fightIDs`, `startTime`, `endTime`,
  `filterExpression`, `includeResources`, `limit`.
- Paged: follow `nextPageTimestamp` until it is null. A long pull needs up to 9 pages for Buffs or Healing.
- Filter syntax confirmed: `ability.id IN (633,1022,...)` (comma-separated, no spaces), used for friendly Buffs
  (one request per cooldown file: defensive and offensive registry IDs) and enemy Casts (encounter-file ability
  IDs); and `type = "heal"` on Healing, not used by ingest yet (2026-10-20, "Cross-fight and heal-only results").

- **Deaths rows name no ability or source.** Every recorded `death` row has `abilityGameID: 0` and `sourceID: -1`;
  the killing blow is in `killingAbilityGameID` and the killer in `killerID`, for example
  `{"type":"death","sourceID":-1,"targetID":10,"abilityGameID":0,"killerID":233,"killingAbilityGameID":1301122}`.
  Normalize fills `ability_id` and `source_id` from those two fields.
- **One request can cover many fights.** `fightIDs` takes a list, and every row's `fight` field names the fight
  it belongs to, so a multi-fight stream splits back into per-fight rows. With 25 fights no row named a fight that
  wasn't requested. Such a request can be slow: one 25-fight request took longer than the client's 30 s timeout,
  and the same request succeeded on a retry (2026-10-24, "Cross-fight and heal-only results").
- `includeResources: true` adds `hitPoints` / `maxHitPoints` to events. About 85 to 90% of friendly `damage`
  events carry them across all 62 recorded pulls; the rest have none, so code must tolerate missing HP.
- Events also carry fields KhadBot ignores (`classResources`, `buffs`, `attackPower`, ...). They make raw pages
  large: a 10-minute pull is about 60 MB of compact JSON, mostly Healing.
- **Healing holds more than `heal` rows.** Across the seven recorded fixture pulls it also has `absorbed` rows
  (81,667: a shield absorbing a hit, named by the shield's spell), `removebuff` rows (14,075, mostly absorb
  shields ending, such as Anti-Magic Shell 48707), a few `removedebuff` rows, and `damage` rows from health
  redistribution (279: Tempered in Battle 469704, Spirit Link). Filtering Healing by `type` therefore drops
  data other code reads: redistribution damage counts as friendly damage and as hits in death windows, and a
  shield's `removebuff` may be its only aura row when Buffs doesn't request that spell.

Checked on the 47 Heroic Ula'tek pulls (2026-10-16); the encounter details are in `docs/decisions.md`, "Log
investigations".

- **A channeling NPC logs one `cast`, then nothing until the channel ends.** Its damage carries on, and some
  damage is never cast at all (an enrage). A quiet enemy Casts page doesn't mean the NPC did nothing:
  `H7z1fqDXjRTFnbtg:15` has no enemy cast in its last 44 s, a 20 s channel followed by an enrage that is never
  cast.
- **A filtered Casts page holds only the filtered IDs.** A cast whose ID isn't in the filter is missing even when
  the damage it leads to is listed: on `H7z1fqDXjRTFnbtg:15` the filtered page's last cast is at 567.4 s and the
  unfiltered page's at 573.7 s.
- **An aura's removal is logged before what removed it.** When a hit or a death takes an aura off a player, the
  `removedebuff` row comes first: the hit 0 to 50 ms later (rarely up to 200 ms), the death up to 200 ms later.
  Match a removal to what follows it, not what precedes it (1,235 removals of one debuff).
- **Friendly Casts carry the aimed target** (`targetID`) of player spells. A cast with no target names actor
  -1, the Environment. Auto-attacks and pet spells appear only on the Enemies side of Threat, and no fetched data
  type records damage done to an NPC: that needs DamageDone, which this file doesn't cover yet.

## Measured cost (2026-10-11)

Recorded with `main.py ingest` (Buffs filtered to the 17 cooldown-registry IDs; enemy Casts unfiltered because
no encounter file existed yet) and `scripts/wcl/spike_wcl_cost.py` (no filters).

| Report | Boss pulls | Points | Wall time | Raw pages | Events | Parquet |
|---|---|---|---|---|---|---|
| `trMgApZCVYkv3jdq` | 25 | 348 | 2 min 40 s | 642 MB | 1.53 M | 20 MB |
| `H7z1fqDXjRTFnbtg` | 16 | 242 | 1 min 50 s | 438 MB | 1.05 M | 13 MB |
| `QkVa6GHRK8yfrTbw` | 21 | 347 | about 3 min | 730 MB | 1.83 M | 22 MB |

- About **14 points per pull** with the default filters, so a full night is a tenth of the hourly budget.
- A request costs about 2 points, plus roughly 1 point per extra page. Unfiltered Buffs on a 526 s pull
  (`trMgApZCVYkv3jdq:26`) took 9 pages, 82.7k events and 10 points; Healing on the same pull took 9 pages,
  89.7k events, 10 points and 37 MB. A one-page request of a small spec costs about 1 point instead: 48 cost 49
  (2026-10-20, "Cross-fight and heal-only results").
- No data type was truncated in any pull.
- Local processing (parse, normalize, store) of the whole 25-pull night from cached pages: **18.4 s** on the dev
  machine, under the 30 s budget. Peak traced memory was about 400 MB, bounded by the largest pull because
  pages are concatenated per pull.
- Parsing one long pull's pages: stdlib `json` 1.58 s, `orjson` 0.56 s. Normalizing it: 2.3 s.
- Digest per pull (no encounter file yet): 0.1 to 0.7 s and about 850 to 1,250 tokens.

## Raw page compression (2026-10-20)

How well zstd compresses the raw pages that hosted mode stores in object storage (`CompressedBlobStore`, `raw/`
only). Measured on a copy of `raw/H7z1fqDXjRTFnbtg` (the 16-pull night above): 453 objects, 439.3 MB, one page per
object as WCL returned it, compressed and decompressed one object at a time in memory with `zstandard` 0.25.0
(single thread, Python 3.14, Windows dev machine). Times are process CPU time, because other jobs were sharing the
machine; read them as relative, not as a benchmark.

| zstd level | Stored | Ratio | Compress | Decompress |
|---|---|---|---|---|
| 1 | 29.2 MB | 15.1x | 375 MB/s | 562 MB/s |
| 3 (zstd default) | 30.6 MB | 14.4x | 253 MB/s | 469 MB/s |
| 6 | 25.5 MB | 17.2x | 68 MB/s | 686 MB/s |
| 9 (KhadBot default) | 22.3 MB | 19.7x | 58 MB/s | 740 MB/s |

- A first run (wall clock, with the machine busy) also timed level 19: 18.0 MB (24.4x), but 1 MB/s to compress, far
  too slow for an ingest.
- By data type at level 3: Healing 328.5 MB to 21.7 MB (15.1x), DamageTaken 73.7 to 5.6 (13.1x), Casts 32.3 to
  2.9 (11.3x); the small types compress 4x to 17x. `report.json` goes from 0.2 MB to 0.03 MB (4.7x).
- Page sizes: median 1.7 kB (the many small types), 90th percentile 4.3 MB, largest 4.7 MB. The largest page takes
  about 16 ms to compress at level 3 and 16 ms to decompress.
- A night of raw pages that is about 440 MB costs about 22 MB at the default level, so storage for raw pages is
  about 5% of the uncompressed size. Compressing the whole night at level 9 takes about 8 s of CPU against a WCL
  fetch of about 2 minutes; reading it all back takes under 1 s.

## Threat data (2026-10-11)

`dataType: Threat` is in the live `EventDataType` enum. Probed on `trMgApZCVYkv3jdq:18` through `events`,
`table` and `graph`. **Nothing in it is a threat amount.** WCL's "threat" is target tracking: who is attacking
whom. `hostilityType` picks the direction.

| | Friendlies: NPCs attacking players | Enemies: players and pets attacking NPCs |
|---|---|---|
| `events` | 348 events, 0.1 MB. NPC melee swings as `cast` with `melee: true`, `abilityGameID: 1` (misses included: 30 Ula'tek swings against 29 melee damage rows); players' taunt casts; the taunt debuff landing on the NPC (`applydebuff`, player to NPC); **NPC** deaths. | 12.7k events, 4.9 MB, 2 pages. `cast` events from players and pets aimed at an NPC, including auto-attacks (4.8k) and pet spells (Fel Firebolt 1.8k); NPC-caused deaths. |
| `table` | Per player, per NPC type: bands of time that NPC was attacking the player (aggro), each with start and end event. 23 kB. | Per NPC, per player or pet: bands of time they were attacking it. 551 kB. |
| `graph` | Per player: how many NPCs were on them, as a count rising by 1 (max 8). | Per NPC: how many players and pets were on it (max 64 on Ula'tek). |

- No event carries an amount: only identity fields, `melee`, instance numbers and the resource snapshot. The
  Enemies side also leaves out healing (no Chain Heal, Prayer of Mending) and tank abilities with no single
  target (Revenge, Thrash, Ironfur), so it is not a feed for computing threat by hand.
- KhadBot fetches the Friendlies `events`, about 1 point per pull. Normalize drops the players' taunt casts
  (they repeat the Casts data) and keeps NPC swings, taunt debuffs and NPC deaths (no other data type has NPC
  deaths; the Deaths fetch is players only). The probe of all four views cost 14.8 points; both `events` sides
  cost 5.
- **WCL's aggro bands start on an NPC melee swing or a taunt debuff landing**, nothing else. On the probe pull
  Shinrael holds Ula'tek from the Growl at 390.74 s to 397.14 s although Ula'tek never swung at Shinrael; the
  Unchecked Rage at 393.5 s falls inside it. A band ends at a switch to another player, the NPC's death, or,
  after a quiet spell, somewhere after its last event. `encounter/analysis/aggro.py` rebuilds the bands from the
  Friendlies events, ending a quiet band at its last event after 20 s without a swing or taunt. Merged per NPC
  type and player and compared with WCL's table on three pulls from three nights:

  | Pull | WCL bands | Exact match | Share of WCL aggro time | Time ours calls aggro that WCL doesn't | Boss time on a different player |
  |---|---|---|---|---|---|
  | `trMgApZCVYkv3jdq:18` | 36 | 35 | 99.1% | 0 | 0 s |
  | `H7z1fqDXjRTFnbtg:20` (kill) | 67 | 65 | 95.1% | 0 | 0 s |
  | `QkVa6GHRK8yfrTbw:20` | 41 | 40 | 99.4% | 0 | 0 s |

  Every difference is WCL keeping a holder longer after the NPC stops swinging: Gore Rattle on Mekerakesh at
  the same point of Stage One in all three pulls (WCL holds about 20 s past the band's start: 132.58 s against
  ours 127.71 s; 131.87 against 114.03; 131.78 against 128.18), and Ula'tek in the kill's last 24 s (WCL holds
  to the pull end). No event in the stored data, the unfiltered enemy casts or the Enemies-side Threat events
  explains where WCL ends those bands. So the bands are reliable for *who* held aggro; how long a holder lasts
  once the NPC goes quiet is a convention. The first two pulls are unit tests against saved tables
  (`tests/fixtures/encounter/wcl_views/`); the third pull is not a recorded fixture.
- WCL's table merges all instances of one NPC type per player (Rawlings 4 and 5 on Mekerakesh are one band);
  the events keep `sourceInstance`, so KhadBot's bands stay per instance.
- Taunt spell IDs seen so far: Taunt 355 (warrior), Growl 6795 (druid), Dark Command 56222 (death knight) and
  Challenging Shout 1161 (area taunt, no single target).

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

## Budget and merged-filter probes

Two scripts measure what the docs above can't say yet. Both spend WCL points, so each prints an estimate and waits
for a typed `yes` before every live step. Neither prints or writes a token. Run them from a checkout whose `.env`
holds `WARCRAFTLOGS_CLIENT_ID` and `WARCRAFTLOGS_CLIENT_SECRET` (a worktree finds the main checkout's `.env`). The
results go in the two subsections below with the date and report code; the follow-ups are rewritten from them.

**Whose budget a user-token request spends** (`scripts/wcl/probe_wcl_budget.py`). It signs you in to Warcraft Logs
with PKCE: your browser opens, and a listener on `http://localhost:8976/callback` catches the redirect (`--port`
changes the port). Register that exact URL as a redirect URL on the WCL client first. The client is used without
its secret unless you pass `--with-secret`, and WCL refuses the token exchange without it (HTTP 400,
`invalid_request`) when the client is not registered as public, so pass it for a normal confidential client. Then it reads `rateLimitData` with the app token and with your token
(twice each, to see what a read costs), fetches the current specs of the shortest recorded pull
(`H7z1fqDXjRTFnbtg:10`, 37 s) with your token, reads both again and prints which counter moved.

    uv run python scripts/wcl/probe_wcl_budget.py

**Whether merging fetch filters pays** (`scripts/wcl/compare_merged_filters.py`). On `trMgApZCVYkv3jdq:26` (526 s;
its metadata comes from the recorded `report.json`, so it costs nothing) it fetches the current specs through
`fetch_pull`, then two merged variants: `hostility` (one request per hostility, no `dataType`, so it first sends
a one-second probe to see whether the API accepts that) and `datatype` (one request per data type and hostility,
the two Buffs filters merged). Per run it reports points, requests, pages, bytes, events and wall time, and
whether the rows normalize to the same frame as the baseline (assigned back to specs by row identity, then
through the real `normalize_pull`). `--max-pages` caps any stream. `--variants` picks a subset.
`--friendly-filter` and `--enemy-filter` replace the `hostility` variant's filters; only the
`ability.id IN (...)` form is known to work (see Events above), so the default Friendlies request is unfiltered.

    uv run python scripts/wcl/compare_merged_filters.py

Two more variants measure their own baseline, the requests they would replace, so they can run without the
single-pull baseline:

- `crossfight` fetches the small specs (Deaths, both Buffs, Debuffs, Dispels, Interrupts, Threat, enemy Casts;
  `--batch-specs` changes the list) once per pull through `fetch_pull`, one pull at a time as ingest does, then
  once per spec with every pull's fight ID. Enemy Casts get one request per encounter filter. The batched rows are
  split back by their `fight` field and compared per pull. `--batch-fights` picks the pulls: `encounter` (every
  pull of `--fight`'s encounter, the default; 25 pulls and 200 baseline requests on `trMgApZCVYkv3jdq`), `all`,
  or a list such as `4,5,6,7,8,9`.
- `healonly` fetches Healing unfiltered, then with `--heal-filter`, and checks that the filtered rows are exactly
  the baseline's `heal` rows. It also reports how many rows and position samples the filter drops. The filter
  has no default; `type = "heal"` is the one confirmed so far. A one-second probe runs first, so a rejected
  filter costs about one request.

`--variant-first` runs these two before their baselines. Run each order once to see whether WCL-side caching
favours whichever goes second.

    uv run python scripts/wcl/compare_merged_filters.py --variants crossfight --batch-fights 4,5,6,7,8,9
    uv run python scripts/wcl/compare_merged_filters.py --variants healonly --heal-filter 'type = "heal"'

### Budget probe results

2026-10-20, `H7z1fqDXjRTFnbtg:10` (37 s), fetched with a user token. It needed `--with-secret`: without it WCL
refused the token exchange with HTTP 400, `error=invalid_request`, so the client is not registered as public.

| Counter read with | Limit per hour | Spent before | Spent after | Moved | Cost of one read |
|---|---|---|---|---|---|
| app token | 3600 | 5 | 18 | 13 | 1 |
| user token | 3600 | 6 | 19 | 13 | 1 |

The script's verdict: both counters moved by the same amount, so one shared pool. The reads interleave (app 5,
user 6, fetch, app 18, user 19) and each read costs 1 point, so the fetch itself cost about 11 points.

Caveat, and the open part: the signed-in user owns the API client. This can't tell "a user token spends the
app's pool" from "the client owner's token shares the client's pool"; another WCL account's token might have its
own budget. A second account's token settles it.

### Merged-filter results

2026-10-20, `trMgApZCVYkv3jdq:26` (526 s), app token. A back-to-back `rateLimitData` read moved the counter by 1.

| Run | Requests | Pages | Bytes | Events | Wall s | Points | Points saved | Time saved | Frames identical |
|---|---|---|---|---|---|---|---|---|---|
| baseline | 11 | 21 | 52156515 | 123799 | 9.8 | 24.31 | | | |
| hostility | rejected: Friendlies/all: WarcraftLogs GraphQL error: Internal server error | | | | | | | | |
| datatype | 10 | 20 | 52156480 | 123799 | 4.1 | 21 | 14% | 58% | True (ignoring tie order: True) |

- **No `dataType` is refused.** The one-second probe of a Friendlies request with no `dataType` got an internal
  server error from the API, so one request per hostility is not possible as specified.
- **Merging same-type requests keeps the rows.** The `datatype` variant (the two Buffs requests as one, with
  their ability IDs unioned) normalized to the baseline's frame exactly: 0 unassigned, 0 missing and 0 ambiguous
  rows. It saved 13.6% of points (21 against 24.31), under the 15% bar: about one request's cost.
- **The 58% time saving is not trustworthy yet.** It ran straight after the baseline fetched the same pull, so
  WCL-side caching may explain part of it. One run each, no repeats.

### Cross-fight and heal-only results

2026-10-20, `trMgApZCVYkv3jdq`, app token, `scripts/wcl/compare_merged_filters.py`. Each variant measured its own
baseline in the same run, and every run normalized to frames identical to its baseline's.

**Cross-fight batching** (`--variants crossfight`): the eight small specs (Deaths, both Buffs requests, Debuffs,
Dispels, Interrupts, Threat, enemy Casts) fetched per pull through `fetch_pull`, against one request per spec
with all six pulls' fight IDs. Run twice on different pulls, once in each order.

| Run | Fights | Order | Requests | Pages | Events | Wall s | Points | Points saved | Time saved |
|---|---|---|---|---|---|---|---|---|---|
| baseline | 4–9 | first | 48 | 48 | 11244 | 7.0 | 49 | | |
| crossfight | 4–9 | second | 8 | 8 | 11244 | 0.5 | 9 | 82% | 93% |
| baseline | 10–15 | second | 48 | 48 | 11258 | 6.0 | 49 | | |
| crossfight | 10–15 | first | 8 | 8 | 11258 | 1.5 | 18.57 | 62% | 76% |

- **Batching keeps the rows.** In both runs every batched row named a requested fight, and assigned back to its
  pull's specs it gave 0 unassigned, 0 missing and 0 ambiguous rows. Each pull's frame matched exactly.
- **A one-page request costs about 1 point**, not the 2 the measured-cost rule of thumb above gives: 48
  one-page requests cost 49 points in both runs.
- **The batched fetch saves 62 to 82% of the small specs' points.** It cost 9 points when it ran second and 18.57
  when it ran first, while the per-pull baseline cost 49 in either order. WCL-side caching seems to favour
  whichever fetch runs second; the 62% from the variant-first run is the conservative figure.

**Whole night, 2026-10-24** (`--variants crossfight --batch-fights encounter --variant-first`): all 25 pulls of
`trMgApZCVYkv3jdq`'s encounter (fights 4 to 28), the batched fetch first.

| Run | Fights | Order | Requests | Pages | Bytes | Events | Wall s | Points | Points saved | Time saved |
|---|---|---|---|---|---|---|---|---|---|---|
| baseline | 4–28 | second | 200 | 200 | 8120915 | 57816 | 26.7 | 201 | | |
| crossfight | 4–28 | first | 8 | 11 | 8114293 | 57816 | 3.0 | 28.91 | 86% | 89% |

- **The saving holds over a whole night**, even with the batched fetch first: 28.91 points against 201, about 8
  points per pull down to about 1.2. The eight streams took 11 pages between them.
- Frames were identical for every pull, with 0 unassigned, 0 missing, 0 ambiguous and 0 stray rows.
- The first attempt failed: one batched request didn't answer within the client's 30 s timeout, and the client
  raised a network error that nothing retries. The points it spent went unrecorded. The rerun above succeeded.

**Heal-only Healing** (`--variants healonly --heal-filter 'type = "heal"'`) on `trMgApZCVYkv3jdq:26` (526 s):

| Run | Requests | Pages | Bytes | Events | Wall s | Points | Points saved | Time saved |
|---|---|---|---|---|---|---|---|---|
| healonly baseline | 1 | 9 | 38857494 | 89685 | 7.7 | 10 | | |
| healonly | 1 | 7 | 32713657 | 64555 | 6.5 | 8 | 20% | 15% |

- **`type = "heal"` is a valid filter expression** and returns exactly the baseline's `heal` rows: 0 extra and 0
  missing rows, identical frames. That confirms the `type = "<event type>"` form for this one value.
- It drops 28% of Healing rows (the `absorbed`, `removebuff` and other rows) and 16% of its bytes, and saves 2
  of 10 points on this pull. Only 73 of 58,902 Healing position samples are lost.
- One run, with the variant second.
