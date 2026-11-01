# WarcraftLogs API: guild, character and server search

What guild search, character search and the server list ask WCL, what each request costs and what KhadBot keeps
between searches. `wcl/search.py` holds the queries; the routes are `api/raids/search.py` and
`api/raids/servers.py` (`docs/hosting.md`, "Guild, character and server search").

## Fields

From WCL's schema documentation, one GraphDoc page per type at
`https://www.warcraftlogs.com/v2-api-docs/warcraft/<type, lowercase>.doc.html` (the source column gives the type).
Labels are quoted from the field descriptions: `[Database Points]`, `[User-Eligible Points]`, or none.

| Need | Field and arguments | Label | Source type |
|---|---|---|---|
| A guild's reports in a time range | `reportData.reports(guildName: String, guildServerSlug: String, guildServerRegion: String, startTime: Float, endTime: Float, limit: Int, page: Int, zoneID: Int, gameZoneID: Int, guildID: Int, guildTagID: Int, userID: Int): ReportPagination`. Times are UNIX ms; `limit` defaults to 100 (1 to 100). `guildTagID` overrides the other guild arguments | **`[Database Points]`** | `ReportData` |
| The page of reports | `ReportPagination { data: [Report] total per_page current_page from to last_page has_more_pages }` | none | `ReportPagination` |
| Per report in a listing | `Report { code title startTime endTime visibility zone fights(...) guild guildTag owner region }`; `visibility` is `'public'`, `'private'` or `'unlisted'`; `fights` takes `difficulty`, `encounterID`, `fightIDs`, `killType`, `translate` | `fights`, `zone`, `guild`, `guildTag`, `owner`: `[User-Eligible Points]`; the rest none | `Report` |
| A guild by name, server and region | `guildData.guild(id: Int, name: String, serverSlug: String, serverRegion: String): Guild` | `[User-Eligible Points]` | `GuildData` |
| Guild name, server and region | `Guild { id name server { name slug region { compactName slug } } }` | `server`: `[User-Eligible Points]` | `Guild`, `Server`, `Region` |
| A guild's faction | `Guild.faction: GameFaction!`, `GameFaction { id: Int! name: String! }` (`name` is localized) | none | `Guild`, `GameFaction` |
| A character | `characterData.character(id: Int, name: String, serverSlug: String, serverRegion: String): Character` | `[User-Eligible Points]` | `CharacterData` |
| A character's recent reports | `Character.recentReports(limit: Int, page: Int): ReportPagination`; `limit` defaults to 10, at most 100. No time-range, zone or encounter argument | **`[Database Points]`** | `Character` |
| A character's guilds and faction | `Character { guilds: [Guild] faction: GameFaction! server: Server! classID }` | none | `Character` |
| Each region's servers | `worldData.regions: [Region]`, `Region { id compactName name slug servers(limit: Int, page: Int): ServerPagination }`; `limit` defaults to 100, at most 5000; `Server { id name normalizedName slug region subregion }`. Also `worldData.region(id: Int)` and `worldData.server(id: Int, region: String, slug: String)` | none (only `Server.guilds` and `Server.characters` are `[Database Points]`; the list doesn't need them) | `WorldData`, `Region`, `ServerPagination`, `Server` |
| Raid zones and their encounters | `worldData.expansions: [Expansion]` (`Expansion { id name zones }`), `worldData.zones(expansion_id: Int): [Zone]`, `worldData.zone(id: Int)`; `Zone { id name frozen encounters difficulties expansion }`; `Encounter { id name journalID zone }`. There is no "current zone" field, and `Zone` has no raid-or-dungeon type | none (`Encounter.characterRankings` and `Character.zoneRankings` are `[Database Points]`) | `WorldData`, `Expansion`, `Zone`, `Encounter` |

- **Both listings spend database points.** Listing a guild's reports (`reportData.reports`) and a character's
  (`Character.recentReports`) are the two `[Database Points]` fields a search uses; KhadBot uses them with the
  user's approval (`docs/decisions.md`). Everything else above is not labelled.
- **Encounter filter.** Neither listing filters by encounter (`reportData.reports` takes `zoneID` only), so a
  character search's encounter is applied to each listed report's fights from the report metadata.
- **Guild URLs.** The schema doesn't describe the site's URLs. A real guild URL,
  `https://www.warcraftlogs.com/guild/us/skullcrusher/death%20and%20destruction`, reads
  `/guild/<region slug>/<server slug>/<percent-encoded name>`; `wcl.search.parse_guild_source` accepts that form only.

## Queries and cost

Each cost is measured on the app token with `rateLimitData.currentRequest` asked in the same request
(`access-and-points.md`, "Reading the counter") by `scripts/wcl/measure_search_cost.py`, and recorded beside each
answer by `scripts/data/record_wcl_search.py` (`khadbotPoints` in `tests/fixtures/encounter/wcl/_search/`).

| Query | Fields | Measured | Reserves |
|---|---|---|---|
| `GuildHeader` | `guildData.guild(name, serverSlug, serverRegion) { id name faction { id name } server { name slug region { compactName slug } } }` | 1 (`application` 0.01, `user` 0.02) | `KHADBOT_SEARCH_POINTS` (1.2) |
| `GuildReports` | `reportData.reports(guildID, startTime, endTime, limit: 15, page: 1) { has_more_pages data { code title startTime endTime visibility zone { id name } } }` | 1.11, **1 database point** (`application` 1, `database` 1, `user` 0.11) | `KHADBOT_SEARCH_POINTS` + `KHADBOT_SEARCH_DATABASE_POINTS` (1) |
| `CharacterReports` | `characterData.character(name, serverSlug, serverRegion) { id name server { ... } recentReports(limit: 15, page: 1) { ... the same report fields } }` | 1, **1 database point** (`application` 0.16, `database` 1, `user` 0.16) | `KHADBOT_SEARCH_POINTS` + `KHADBOT_SEARCH_DATABASE_POINTS` |
| `Regions` | `worldData.regions { id compactName name slug }` | 1 | Not sent by the API ("Server list") |
| `RegionServers` | `worldData.region(id) { servers(limit: 300, page) { has_more_pages data { id name slug } } }` | 3.01 a page | Not sent by the API |

The budget settles a request at its reservation, so the reservations sit just above the measured costs. Each listed
night also needs its report metadata, 2.01 points when it isn't cached (`report-data.md`, "Report metadata").

## What a search costs

On the app token, with measured costs, 15 reports at the cap, and access checks at 1 point each (every request costs
at least 1; the check itself is not measured):

- **First guild search, nothing cached:** the guild header 1, the listing 1.11 and 1 database point, 15 nights'
  metadata 30.15 and 15 public access checks 15: about 47 points and 1 database point.
- **Again within the listing window** (`KHADBOT_SEARCH_LISTING_MAX_AGE_S`, 15 minutes), by anyone without a WCL
  sign-in: nothing, as long as the nights' metadata stands (a finished log's for good, a live log's for two minutes;
  `report-data.md`, "Report metadata").
- **After the window, within six hours:** the listing only, 1.11 points and 1 database point. The guild header is
  kept for good and the public access answers stand six hours (`KHADBOT_ACCESS_PUBLIC_RECHECK_S`).
- **After six hours:** the listing plus the 15 access checks, about 16 points and 1 database point.
- **A signed-in user with a WCL token** lists on their own token, so their listing is their own (in memory, the same
  window) and their access answers stand an hour (`KHADBOT_ACCESS_RECHECK_S`).
- **A character search:** the character and its listing are one request (1 point, 1 database point), asked again
  after the listing window; the nights cost as above.

The cap matters for an active raiding guild: Death and Destruction (guild 27112) had more than 15 reports in 30
days, and Dastard more than 15 recent reports (`has_more_pages` true for both in the recordings).

## Caching

`api/raids/search_cache.py`, in the blob store under `search/` (outside the expiring `raw/` prefix) unless noted:

- **Identity.** A guild's ID, name, server, region and faction, and a character's ID, name, server and region, are
  kept for good, keyed by region, server and lower-case name. "No such guild or character" stands
  `KHADBOT_SEARCH_NOT_FOUND_MAX_AGE_S` (an hour), so a mistyped name costs nothing twice while a guild WCL creates
  with its first upload still shows up within the hour.
- **Listings on the app token** hold public reports only and are shared by every caller without a WCL sign-in for
  `KHADBOT_SEARCH_LISTING_MAX_AGE_S` (`search/listings/`).
- **Listings on a user's own token** may hold that user's private reports, so they stay in memory for that user only.
- **Access.** Every listed report passes the report access check on every read; one the caller can't read is left
  out and counted (`left_out`). A cached "public" answer stands `KHADBOT_ACCESS_PUBLIC_RECHECK_S` (six hours), a
  user-token answer `KHADBOT_ACCESS_RECHECK_S` (an hour), a denial `KHADBOT_ACCESS_DENIED_RECHECK_S` (five minutes).

## Server list

The API never asks WCL for servers: the US and EU list ships as `config/registries/servers.yaml`, which
`scripts/data/build_server_list.py` builds from the recorded server pages (`tests/fixtures/encounter/wcl/_search/`)
or, with `--live` and after a typed `yes`, from about 7 points of live requests. The API loads it at start-up and
refuses a search naming a server not in it before any request.

- **One query for every region's servers is refused.** At limit 5000 WCL answers with a GraphQL error, "Max query
  complexity should be 50000 but got 125021", so the script reads one region at a time (`region(id).servers`).
- **A page's cost follows the limit asked, not the servers returned:** limit 1000 cost 10.01 points for 247 US
  servers; limit 300 cost 3.01 for 268 EU servers. The script asks 300 to a page.

## Known alternatives

- **Guild attendance** (`Guild.attendance`, user-eligible, no database points) lists a guild's raid nights, but it
  misses nights logged outside the guild and names players without a server, so search uses the report listings.

## Open questions

- How WCL answers an unknown guild or character: a null or a GraphQL error (both map to 404).
- What an exhausted database-point cap returns. An error whose message says "rate limit" is read as a budget wait;
  any other listing error answers 503.
