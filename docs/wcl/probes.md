# WarcraftLogs API: budget and merged-filter probes

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
`ability.id IN (...)` form is known to work (see `report-data.md`, Events), so the default Friendlies request is unfiltered.

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

A rerun on 2026-10-24 signed in with the same account (the browser was already signed in) and gave the same
result: both counters moved by 13.

The signed-in user owns the API client, so these runs alone couldn't tell "a user token spends the app's pool"
from "the client owner's token shares the client's pool". WCL's point-type documentation answers it instead
(`access-and-points.md`, "Rate limits and point types"): the current scheme's settings page tracks no user-eligible spending, which
fits both runs, and the new one spends a signed-in user's own small pool first on user-eligible fields. The probe
worth running now is one on the new scheme, after opting in, to see the user pool's size and how `rateLimitData`
reports it.

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
