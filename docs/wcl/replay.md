# WarcraftLogs API: WCL replay

## WCL replay

`wcl/replay.py` answers WCL requests from recorded reports instead of the network, for development and tests
(settings and use: `docs/hosting.md`, "WCL replay"). It is an `httpx` transport under `WarcraftLogsClient`, so every
caller's own code runs unchanged. Everything it answers comes from a recording; what it can't answer it refuses by
name. How each request maps to the recording (a directory laid out like the raw page cache):

| Request | Answer |
|---|---|
| `POST` token URL, `grant_type=client_credentials` | A placeholder app token, locally. |
| `POST` token URL, any other grant (sign-in code exchange, refresh) | Sent to real WCL. |
| `CURRENT_USER_QUERY` (`userData { currentUser }`) | Sent to real WCL. |
| `RATE_LIMIT_QUERY` | The simulated counter: `limitPerHour`, `pointsSpentThisHour`, `pointsResetIn` (seconds). |
| `query GetReportMeta` `{code}` | `<code>/report.json` as `reportData.report`, without KhadBot's own `khadbot*` keys. For the live report, only its fights that have recorded pages, revealed one at a time in start order. |
| `query ReportReadable` `{code}` | `{startTime}` from `<code>/report.json`. |
| `query GetEventsPage`, one fight `f` | `<code>/<f>/<dataType>[-Enemies]/<filter_hash>/page_NNN.json` as `reportData.report.events`, the bytes as recorded. `page_000` answers `startTime` equal to the fight's `startTime` in `report.json`; `page_N` answers `startTime` equal to `page_N-1`'s `nextPageTimestamp`. `endTime` must be the fight's `endTime`. |
| `query GetEventsPage`, several fights | `<code>/batch/<dir>/<filter_hash>/<fights_hash>/page_NNN.json` (`fights_hash`: the first 12 hex of SHA-256 of the sorted IDs joined by commas), from the earliest fight's `startTime` to the latest's `endTime`, chained the same way. Refused when no such stream was recorded. |
| A `code` with no `<code>/report.json` | HTTP 200 with a GraphQL error and a null report, as WCL answers a missing report. WCL's own wording is not recorded; replay's message ("WCL replay has no report '...'") avoids "private", "forbidden" and "rate limit", so the client raises a plain `WarcraftLogsAPIError` and the access check reads it as not readable. |
| `query GuildHeader`, `GuildReports`, `CharacterReports` | The whole recorded GraphQL body (errors included) under `_search/`: `guilds/<region>/<server>/<name>.json` and `characters/...` the same way (name lower case, percent-encoded); `guild_reports/<guild id>.json`, whatever window and limit are asked (the caller applies its own window too). Written by `scripts/data/record_wcl_search.py`; plain git, not LFS. The server queries are not answered: the API doesn't send them. |
| Anything else | `WclReplayRefusedError`, naming the report, fight, spec, filter hash and start time, the query, or the URL. |

- `filter_hash` is the raw cache's: the first 12 hex of SHA-256 of the filter expression, `none` without one. A
  refused page names it, so a filter no recording holds is visible at once.
- Pages keep their recorded boundaries whatever `limit` the request sends. The recordings were made with 10,000.
- Which requests arrive is decided above replay: under replay, ingest asks for the recorded fetch shape (the
  recordings' filters, per pull; `docs/hosting.md`, "WCL replay"), because the current registries' filters have
  hashes no recording holds.
- The recorded fixtures' `report.json` files have no `phases` (they were recorded before `GET_REPORT_META` asked
  for them), so replayed metadata has none for them, just as offline ingest of them has none.
- **Charges:** 1 point per answered request (`costs.md`, "Measured cost": a one-page request costs about 1, every
  request at least 1), a `rateLimitData` read showing the count before its own point (`probes.md`, the budget probe's
  interleaved reads), one pool for app and user tokens (the current scheme). HTTP 429 when no point is free, with no
  `Retry-After`, since WCL isn't known to send one. Refusals cost nothing.

## Open questions

- **A missing report's wording.** WCL's GraphQL error for a missing or unreadable report is not recorded, so replay
  words its own.
