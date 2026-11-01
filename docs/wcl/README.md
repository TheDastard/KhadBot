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

| File | Covers |
|---|---|
| [access-and-points.md](access-and-points.md) | Tokens, user sign-in, the readability check; rate limits, point types and charging rules |
| [report-data.md](report-data.md) | Report metadata fields and phase IDs; events (arguments, paging, row quirks); threat data |
| [positions.md](positions.md) | NPC instances and position samples: what carries a position, scale, freshness, uses |
| [costs.md](costs.md) | Measured ingest cost, raw page compression, live watch poll cost |
| [probes.md](probes.md) | The budget and merged-filter probe scripts and their results |
| [replay.md](replay.md) | How WCL replay answers each request from recordings |
