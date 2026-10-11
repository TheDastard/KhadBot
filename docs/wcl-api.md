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
| `report.phases` | Per encounter: `phases { id name isIntermission }` and `separatesWipes`. This is what maps phase IDs to names. |

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
- Filter syntax confirmed: `ability.id IN (633,1022,...)` (comma-separated, no spaces). Used for friendly Buffs
  (cooldown registry IDs) and enemy Casts (encounter-file ability IDs).

- `includeResources: true` adds `hitPoints` / `maxHitPoints` to events. About 85 to 90% of friendly `damage`
  events carry them across all 62 recorded pulls; the rest have none, so code must tolerate missing HP.
- Events also carry fields KhadBot ignores (`classResources`, `buffs`, `attackPower`, ...). They make raw pages
  large: a 10-minute pull is about 60 MB of compact JSON, mostly Healing.

## Measured cost (2026-10-11)

Recorded with `main.py ingest` (Buffs filtered to the 17 cooldown-registry IDs; enemy Casts unfiltered because
no encounter file existed yet) and `scripts/spike_wcl_cost.py` (no filters).

| Report | Boss pulls | Points | Wall time | Raw pages | Events | Parquet |
|---|---|---|---|---|---|---|
| `trMgApZCVYkv3jdq` | 25 | 348 | 2 min 40 s | 642 MB | 1.53 M | 20 MB |
| `H7z1fqDXjRTFnbtg` | 16 | 242 | 1 min 50 s | 438 MB | 1.05 M | 13 MB |
| `QkVa6GHRK8yfrTbw` | 21 | 347 | about 3 min | 730 MB | 1.83 M | 22 MB |

- About **14 points per pull** with the default filters, so a full night is a tenth of the hourly budget.
- A request costs about 2 points, plus roughly 1 point per extra page. Unfiltered Buffs on a 526 s pull
  (`trMgApZCVYkv3jdq:26`) took 9 pages, 82.7k events and 10 points; Healing on the same pull took 9 pages,
  89.7k events, 10 points and 37 MB.
- No data type was truncated in any pull.
- Local processing (parse, normalize, store) of the whole 25-pull night from cached pages: **18.4 s** on the dev
  machine, under the 30 s budget. Peak traced memory was about 400 MB, bounded by the largest pull because
  pages are concatenated per pull.
- Parsing one long pull's pages: stdlib `json` 1.58 s, `orjson` 0.56 s. Normalizing it: 2.3 s.
- Digest per pull (no encounter file yet): 0.1 to 0.7 s and about 850 to 1,250 tokens.
