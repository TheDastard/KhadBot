# WarcraftLogs API reference

How KhadBot uses the WarcraftLogs (WCL) v2 GraphQL API: access, how requests are charged, each query KhadBot sends
and what its fields mean, measured costs, caching, replay, and what is still unknown. Every fact names its source:

- **WCL's schema documentation**, one GraphDoc page per type at
  `https://www.warcraftlogs.com/v2-api-docs/warcraft/<type, lowercase>.doc.html`, and its "Rate Limits" and "Point
  Types" pages. They refuse scripted fetches (HTTP 403); open them in a browser.
- **A recorded report**, by code and fight ID (`H7z1fqDXjRTFnbtg:15`), so the fact can be checked again.
- **A measurement script** under `scripts/wcl/`, named beside the number it produced.

Field labels are quoted from the schema: `[Database Points]`, `[User-Eligible Points]`, or none (plain application
points). How to update these files is in `.claude/rules/docs.md`; each ends with its open questions.

| File | Covers |
|---|---|
| [access-and-points.md](access-and-points.md) | Tokens, user sign-in, the readability check; rate limits, point types, charging rules and reading the counter |
| [report-data.md](report-data.md) | The report metadata query (who sends it, cost, caching, fields, phase IDs); events (arguments, paging, what rows hold); threat data |
| [positions.md](positions.md) | NPC instances and position samples: what carries a position, scale, freshness, what positions answer |
| [search.md](search.md) | Guild, character and server search: schema fields, queries and measured costs, caching, the server list |
| [costs.md](costs.md) | Measured ingest cost, raw page compression, live watch poll cost |
| [probes.md](probes.md) | The budget and merged-filter probe scripts and their results |
| [replay.md](replay.md) | How WCL replay answers each request from recordings |

## Reference reports

Public, Heroic Ula'tek progression, zone The Venomous Abyss:

| Code | Night | Content |
|---|---|---|
| `QkVa6GHRK8yfrTbw` | 9/29, learning | Nek'zali kill, 6 Coiled Altar pulls (kill on fight 11), 14 Ula'tek wipes, 4 trash fights |
| `trMgApZCVYkv3jdq` | 10/1, progression | 25 Ula'tek wipes, same 22 players every pull, 3 trash fights |
| `H7z1fqDXjRTFnbtg` | Final night | Nek'zali and Coiled Altar kills, 8 Ula'tek pulls (kill on fight 20), 6 Mythic Nymrissa wipes, 14 trash fights |
