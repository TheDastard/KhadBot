# WarcraftLogs API: report metadata, events and threat

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
  IDs); and `type = "heal"` on Healing, not used by ingest yet (2026-10-20, `probes.md`, "Cross-fight and heal-only results").

- **Deaths rows name no ability or source.** Every recorded `death` row has `abilityGameID: 0` and `sourceID: -1`;
  the killing blow is in `killingAbilityGameID` and the killer in `killerID`, for example
  `{"type":"death","sourceID":-1,"targetID":10,"abilityGameID":0,"killerID":233,"killingAbilityGameID":1301122}`.
  Normalize fills `ability_id` and `source_id` from those two fields.
- **One request can cover many fights.** `fightIDs` takes a list, and every row's `fight` field names the fight
  it belongs to, so a multi-fight stream splits back into per-fight rows. With 25 fights no row named a fight that
  wasn't requested. Such a request can be slow: one 25-fight request took longer than the client's 30 s timeout,
  and the same request succeeded on a retry (2026-10-24, `probes.md`, "Cross-fight and heal-only results").
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
