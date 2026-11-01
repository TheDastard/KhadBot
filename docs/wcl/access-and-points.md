# WarcraftLogs API: access and points

## Access

- Client-credentials token from `https://www.warcraftlogs.com/oauth/token`; queries go to `/api/v2/client`.
  Private reports need a user token and `/api/v2/user`.
- Requests spend rate-limit points; `rateLimitData { limitPerHour pointsSpentThisHour pointsResetIn }` reads the
  counter. See "Rate limits and point types" below.
- User sign-in (`wcl/oauth.py`) is the OAuth 2.0 authorization code grant with PKCE:
  - The authorize page is `https://www.warcraftlogs.com/oauth/authorize`, with `client_id`, `redirect_uri`,
    `response_type=code`, `code_challenge`, `code_challenge_method=S256` and `state`. Reading reports needs no
    `scope`; `KHADBOT_WCL_SCOPE` sends one if a later use needs it.
  - The code is exchanged at the token URL with `grant_type=authorization_code`, `code`, `code_verifier`,
    `redirect_uri`, `client_id` and the client secret. WCL treats the client as confidential and refuses the
    exchange without the secret (HTTP 400, `invalid_request`). The response carries a refresh token alongside the
    access token.
  - Access tokens are renewed with the standard `refresh_token` grant. WCL doesn't document whether a refresh
    returns a new refresh token, so keep the old one when the response has none.
  - The signed-in user is `userData { currentUser { id name } }`, which works on the user endpoint only.
  - The refresh grant and `currentUser` haven't been exercised against WCL yet (see `docs/follow-ups.md`).
- Whether a token can read a report: `reportData { report(code) { startTime } }`. A null report or a GraphQL error
  means it can't; a network failure, an HTTP error or a rate limit leaves the answer unknown.

## Rate limits and point types

Source: WCL's API client settings page and its "Rate Limits" and "Point Types" documentation, read on 2026-10-24
and 2026-10-25. WCL's documentation pages refuse scripted fetches, so check them by hand when this matters.

### Limits

- **Per account, per 60-minute window.** One allowance covers every API client the account owns; a second client
  on the same account adds no points.
- **Two schemes.** WCL is moving accounts from its current limits to new ones. An account opts in with "Use New
  Limits" on the client settings page; API keys created on or after 2026-09-29 start on the new scheme.

| Points | Current limits | New limits |
|---|---|---|
| Request (application) points | 3,600 an hour | 1,000 an hour |
| ↳ split into app-only and user-eligible spending | not tracked | tracked |
| Database points | not tracked | 250 an hour |
| User points | not shown | a small pool per signed-in user |

- **Subscriptions raise the limits** of the account that owns the client: Silver ($2 a month) more database
  points, Gold ($5) 3x the API points, Platinum ($10) 9x, Archon Alchemical Society ($25) Platinum's benefits.
  Prices as listed on 2026-10-25.

### Point types

- **Application points** are the default. Every request spends them unless user points cover it.
- **Database points** are application points spent on fields the schema labels `[Database Points]`: fields backed
  by expensive database queries, such as rankings and report filtering. They count toward the application limit
  and also have their own, smaller limit. When that runs out, labelled fields return errors until the window
  resets; other fields keep working.
- **User points** are spent in addition to application points, by requests made with a user's token (an
  authorization code or PKCE sign-in, sent to `/api/v2/user`) on fields the schema labels `[User-Eligible Points]`.
  - Eligible: most report data (events, tables, graphs) and many character and guild fields, such as guild
    attendance and membership lists and character name, server and region.
  - Never eligible: rankings, even a character's, and anything through the v1 API, which has no user sign-in. A
    client-credentials token never spends user points.
  - Each user's pool is small. WCL spends it first and falls back to the application's points when it is empty,
    so the user doesn't hit a 429 while the application has points left.
  - `@onlySpendUserPoints` on a query (`query @onlySpendUserPoints { ... }`) turns the fallback off: the request
    fails rather than spend application points.

### Charging rules

- Every request costs at least 1 point.
- Before running a request, WCL holds 1 application point. If the request succeeds and spends only user points,
  the hold is released and the minimum charge goes to the user. So a client needs at least 1 application point
  free to send any request, even one the user pays for in full.
- The limit applies per window; `rateLimitData.pointsResetIn` says when the current one ends.

### Commercial use

An app that uses database-point fields needs WCL's explicit approval before it is commercialised.

### Not yet known

- The size of a user's pool, and whether it differs by the user's own subscription.
- What `rateLimitData` reports on the new scheme: whether it shows user and database points, and whether a read
  with a user token shows that user's pool or the application's.
- Which label each field carries. The schema marks them one by one; the lists above are WCL's examples.
