# KhadBot follow-ups

Open work that is not a plan task yet. One item per gap, deleted in the commit that fixes it or decides against
it (`.claude/rules/docs.md`). Encounter-specific items live in `docs/encounter/follow-ups/`, one file per raid,
under the encounter's heading, which the encounter file's header comment points to. Decisions and their reasons
are in `docs/decisions.md`.

Current track: the hosted service ([work plan](https://claude.ai/artifact/KaomaNz2SSroTk45DSy9zv)), then the web
dashboard ([first-round plan](https://claude.ai/artifact/9Ns4vbDzBAjpq3Fy96LVC7)) and the agents on the web.

## Owed by a person

- **Cooldown durations.** Fill `duration_s` in `defensive_cooldowns.yaml` (most are null).
- **Golden questions and eval thresholds.** `tests/golden/questions.yaml` is empty; `expected_refs_any` can only
  come from digests of ingested real pulls (the fixture pulls and the full nights in `KHADBOT_DATA_DIR`).
- **Engineering-principles doc.** The external doc still predates this design.
- **Plan section 7 wording.** The encounter-file task names the old `config/encounters/<id>.yaml` path; section
  6.2 has the current layout.

## Cooldown registries

- **Scripted cooldowns the data can't classify.** The review file's `scripted` list (63 spells) needs a person:
  Survival Instincts, Lichborne, Healing Tide Totem, Vampiric Embrace, Stasis (370537), Time Spiral and others.
- **Offensive file scope.**
  - Damage abilities and enemy debuffs on long cooldowns (Frostwyrm's Fury, The Hunt, Fury of Elune, Execution
    Sentence, Divine Toll, Touch of the Magi, Reaper's Mark, Touch of Death): decide whether the file takes them.
  - Left-out candidates: Nature's Swiftness, Invoke Yu'lon, Avenging Crusader and Apotheosis are healing
    cooldowns (defensive file if anywhere); Incarnation: Guardian of Ursoc raises maximum health 30% and may be
    defensive; Heart of the Wild's duration is unknown.
  - Bladestorm is cast as 446035, which SimC doesn't list as a player spell.
  - Racials and potions are cast every pull but the file is per class; decide whether and with which `class`.
  - SimC has no player spell for Icy Veins, Storm, Earth, and Fire, Coordinated Assault, Summon Gargoyle and about
    fifteen more, and none was cast on the recorded nights. Confirm in game that they are gone or passive.
  - The heuristic can't tell a damage spell modifier from a healing one; reading spell class flags would.
- **Utility cooldowns SimC can't find.** Add by hand: the battle resurrections, Mana Tide Totem, Symbol of Hope,
  Wind Rush Totem, Time Spiral and Rescue.
- **Friendly damage nobody has classed.** Price of Progress (1233429), Guardian's Hide (1272097), Fiery Rush
  (365199), Blessing of Dawn (210380), Shadow Mend (186439), Void Leech (451963), Killing Spree (424564); Stagger,
  Roar of Sacrifice and Cauterize have proposed classes, unverified.

## Encounter analysis

- **Offensive cooldown analysis.** Only casts are listed: no usage efficiency (needs verified cooldowns), no
  alignment with burst phases or windows, no casts wasted in intermissions.
- **Utility auras aren't fetched.** Ally auras need a third Buffs filter, enemy ones an Enemies Debuffs request
  whose cost no fixture has measured. Add when an analysis needs durations or who was stunned.
- **The analyst can't reach most cooldown casts.** A 10-minute pull casts about 320 cooldowns; the digest shows 46
  and `cooldown_timeline` returns at most 25 rows filtered by phase only. Choose between exposing the full stream
  with filters and paging, per-spell summary records, or both
  ([sizes and options](https://claude.ai/code/artifact/5a2d02ed-c264-4e0a-8f2d-a79e0f86fff3)).
- **Wrong-target damage.** Design settled (`docs/encounter/recorded-nights.md`, "Log investigations"; contract text
  in `docs/plan-changes.md`, `encounter/spike-logs`): (1) check friendly DamageDone's fields, pages and points on
  one long pull and record them in `docs/wcl/report-data.md`; (2) add the fetch spec; (3) add `kill_targets`, an
  `off_target_share_gt` rule, a `KILL TARGETS` digest section and a `kill:<id>` ref; (4) author Ula'tek's Venomous
  Heart entry.
- **Other uses of positions.** Only per_cluster blame uses them. Not built: who stood in Volatile Purge or a soak,
  formation checks (stacking, splits, subgroups from a raid plan), an analyst position tool, positions in the
  dashboard.
- **Positions on encounters that change maps.** No recorded pull has several maps. When one is recorded, check
  `mapID` changes at the move; anything over time must split samples at a map change, and blame assumes an NPC
  stays where it was last sampled.
- **Failures charged to no one.** Occurrences with no aggro holder and soak failures need raid-plan assignments
  (plan section 14).
- **Killer instance number.** Normalize doesn't map `killerInstance`, so `killer_name` has no `#57`. Mapping it
  changes every pull's events hash; do it with the next normalize change.
- **Re-ingest local nights for positions.** Nights ingested before the position table read `no_position`:
  `python main.py ingest <code> --offline` for `H7z1fqDXjRTFnbtg`, `QkVa6GHRK8yfrTbw` and `trMgApZCVYkv3jdq`.
- **A stored read still loads the annotated frame.** `RecordsStore.get` reads it even for callers that want only
  the digest; make `PullRecords.annotated` lazy if a view needs that.
- **The CLI's `/review` doesn't use `ReviewPull`.** It needs the cross-pull synthesis `ReviewPull` leaves out; give
  the graph a way to synthesize from findings it is handed if `/review` should report per-pull progress.

## Pull reviews

- **Measure the prompt-cache hit rate and the cost per pull.** Review a recorded night on the real analyst model
  (it spends money: ask first) and record both in `docs/decisions.md` (D98), replacing the architecture page's
  estimate. The usage ledger already holds cached and cache-write tokens per call.
- **Choose the analyst model and the quota.** The worker uses whatever `ANALYST_MODEL` or the provider setting names;
  `KHADBOT_REVIEWS_PER_DAY` defaults to 10 until the real numbers are set.
- **`jobs.reviews.analyst_model_id` repeats `llm_factory`'s provider and model resolution** so the API can name the
  model without building a client. Move it into `llm_factory` (one function both use) so they cannot drift.
- **A running review can't be cancelled.** It stops only at the next model call's progress report; the cancel route
  answers 404 for review jobs.

## WCL fetching and points

- **Choose a limit scheme and tier.** KhadBot is on the current 3,600 points an hour; the new scheme gives 1,000
  application and 250 database points plus a per-user pool, and Gold or Platinum subscriptions multiply them.
  Before opting in, check the user-eligible label on every field ingest reads; after, run the budget probe with a
  non-owner account to learn the user pool's size and what `rateLimitData` reports, then set
  `KHADBOT_BUDGET_USER_POINTS_PER_HOUR` and turn `KHADBOT_BUDGET_USER_TOKENS_SPEND_APP` off. A night is about 180
  points with batching.
- **WCL approval for commercial use.** Confirm against the schema that ingest uses no database-point fields, and
  ask WCL before any paid tier, with the questions on storing and serving derived data.
- **WCL's limit on filter length** is unknown; the defensive Buffs filter holds 95 IDs.
- **Batched fetch gaps.** No fixture test shows batched frames equal per-pull frames (build batch streams from
  the recorded pages and compare). Live ingests are batched too; record that in D55 or send them per pull. The
  saving hasn't been measured through the shipped code path.
- **Filter Healing to `type in ("heal", "damage")`.** Saves about 20% of Healing's points and kept every record on
  the fixtures apart from one Buffs-filter artifact. Before setting it: probe the `in (...)` form live, pin recorded
  ingest to unfiltered Healing, and check an absorb defensive still shows from Buffs alone.
- **Sign-in details unconfirmed.** Whether a refresh rotates the refresh token, and WCL's error text for a private
  or missing report, are unrecorded. A retried ingest re-reads the stored token rather than forcing a refresh.
- **WCL ability icons unconfirmed.** `masterData.abilities.icon` is in the query but no recording has it.
- **Report guild and visibility are not fetched.** `reports.guild_id`, `guild_name` and `visibility` stay empty, so
  the per-guild budget allowance and the usage ledger's guild column fall back. Add them to the report query.
- **A failed app-token request isn't remembered.** With bad client credentials one ingest asked WCL's token
  endpoint 155 times in 12 s (once per fetch); the stack does this, since `deploy/stack.env` holds placeholder
  credentials and its comment wrongly says the stack makes no WCL calls (the worker's budget sync and any ingest of
  an unloaded pull call WCL).
- **Character matching by name.** Metadata gives no server per actor, so a same-named player from another server
  matches; ask for the actor's `server` if the schema has it.

## Jobs, budget and retention

- **Stalled jobs.** A worker that dies (crash, out of memory, `--reload` on Windows) leaves its jobs `running`.
  Fail and requeue stalled jobs when a worker starts or periodically (`JobManager.get_stalled_jobs`).
- **Nothing prunes finished rows.** Finished `jobs` rows, procrastinate's jobs (`builtin:remove_old_jobs`), expired
  `sessions` and `oauth_states`, and read-model rows of pulls that left a report all stay.
- **Retention gaps.** The lifecycle script and records cleanup have run only against moto and fakes; run the
  script against R2 on staging. A backfill selects every projected pull, current ones included.
- **No worker test with a fixture report through the stack.** The worker uses configured filters and SYNTH01
  needs its own; letting a test point the worker at fixture config would close it.
- **Live watch gaps.** Poll cost is 2.01 points but `KHADBOT_WATCH_POLL_POINTS` defaults to 1: set 2. Whether WCL
  lists an in-progress fight with a future end is unverified. A watch stops when its user signs out rather than
  falling back to the app token. A watch linked late ingests every past fight from the headroom.
- **WCL budget gaps.** Concurrent jobs are charged each other's points (no per-request cost from WCL). A job that
  fails before any request is charged its estimate. A cancelled waiting job keeps its place in line until reached.
- **Usage ledger gaps.** A failed ingest writes no WCL row. The default `ANTHROPIC_MODEL`
  (`claude-sonnet-4-20250514`) has no price in `config/pricing/models.yaml`. Nothing warns when prices go stale.
- **A Postgres restart breaks the stack until restarted.** The API's pool (`db/connect.py`) hands out a closed
  connection (one 500 `AdminShutdown`, no pool `check`), and the worker exits 0 and stays down (`restart: "no"`).
  Seen once when `stack:seed` recreated the postgres container right after `stack:up`; not reproduced.
- **Cancelling an ingest whose process jobs are all still queued ends it `done`.** `_cancel` in `jobs/status.py`
  drops the children before it flags the root, so `_settle` runs with `cancel_requested` still false.
- **Worker health is per machine.** `main.py worker --check` reads a local file; a heartbeat row would show other
  machines.

## Logs and observability

- **Error tracker service not chosen.** The API, worker and web app report through the Sentry SDKs once a DSN is
  set; whether that is Sentry's hosted plan or GlitchTip run ourselves is open (D116), and no DSN is set anywhere.
- **Hosted logs aren't collected.** Staging and production lines stay on the platform's stdout. It waits on the
  hosting platform and on choosing Grafana Cloud or a Loki run ourselves (D113). `config.alloy` finds containers
  through the Docker socket of the `khadbot` compose project, which a platform doesn't offer: use the platform's log
  drain or an Alloy sidecar. Measure line volume on staging before choosing retention.
- **Hosted deploys set neither `KHADBOT_ENV` nor `KHADBOT_VERSION`.** Only `deploy/stack.env`, the dev loop and CI's
  `GIT_SHA` build argument set them; without `env=staging`/`prod` the dashboards' env picker can't tell environments
  apart, and without a version no line names the build that wrote it.
- **The read-only Postgres role exists only on the local stack.** `deploy/observability/postgres/readonly.sql` runs
  from the `obs-readonly` compose service. A hosted database needs the role made by an operator step, its password in
  the platform's secrets, Grafana's `khadbot-pg` data source over TLS, and a read replica if dashboard queries load the
  primary.
- **No hosted Grafana access model.** The local Grafana lets anonymous users act as Admin because it binds to
  127.0.0.1. A hosted one needs real sign-in, a decision on who gets access, and data sources provisioned with the
  UIDs the dashboards name (`loki`, `khadbot-pg`).
- **Hosted log retention isn't set.** Lines hold WCL user IDs and report codes; choose how long they're kept and
  record it in `docs/hosting/operations.md` beside the bucket retention.
- **Alerts have no contact point.** The rules show only in Grafana's Alerting list until a channel is chosen.
- **Not yet: traces** (OpenTelemetry to Tempo, with `trace_id` in the log contract) **and Postgres slow-query
  logging** (`log_min_duration_statement` on staging, collected by Alloy).

## Storage and read models

- **No S3 service in CI.** `tests/integration/hosted/test_blob_s3.py` skips without `KHADBOT_S3_BUCKET`; add a
  SeaweedFS service and its variables to the `hosted` job.
- **No way to move a local data directory into a bucket.** A plain upload of `raw/` lacks the `.zst` suffix; a copy
  command should put every key through `default_blob_store()`.
- **Read-model query plans not captured.** Run `pytest tests/integration/hosted -m hosted -s -k query_plans` on a
  seeded database and check progression uses `pulls_encounter`.
- **Read-model equality runs on SYNTH01 only in CI** (no LFS in the `hosted` job).
- **Night summary on large reports in local mode.** The 32-pull records LRU makes a 47-pull night rebuild most pulls
  per request; cache `night.PullFacts` by records hash rather than raising the LRU.

## Hosted API

- **No hosting platform chosen** for the API, the worker and the web app. Every hosted item in "Logs and
  observability", the web app's "Not deployed" and "Worker health is per machine" wait on it;
  `docs/hosting/observability.md` ("Open questions") lists the decisions due with it.
- **Report lookup leftovers.** `api/jobs.parse_source` duplicates `wcl.reports.parse_report_source`. A watch poll
  writes `report.json` without `khadbotFetchedAt`, so the next lookup refetches. `ready` reads the whole report
  where a query for projected fight IDs would do.
- **`GET /v1/reports` doesn't re-check access age**, so a report made private stays listed until reopened.
- **A cancelling job is followed.** `POST /v1/reports` follows a covering job with `cancel_requested` set; start a
  new one instead.
- **`ready` ignores records inputs**, so after a `RECORDS_VERSION` bump a loaded pull opens through the digest
  route's rebuild.
- **Encounter list for the character search.** The API serves no list of the current raid's encounters, so the
  select offers only encounters seen this visit. Add a route from the encounter files.
- **Damage-window panel's other occurrences** need each occurrence's active cooldowns: resolve sibling refs, or add
  `defensive_cooldowns_active` to `WindowMarker`.
- **Wowhead script on a client-rendered page.** Confirm `WH.Tooltips.refreshLinks(element)` and the CSP hosts in a
  browser.

## Web dashboard

- **Opening-a-pull tile over art.** The canvas darkens the thumbnail with a gradient and sets the label and fight %
  in white; the build puts them on translucent surface chips in the quality colour. Matching it needs scrim and
  on-art text tokens in both themes.
- **Phone layouts.** The 390-wide artboards: rail selector, panel as bottom sheet, account sheet, phone sizes,
  phone-width Playwright and axe runs.
- **Not deployed.** Add the web app as its own service with the deploy pipeline (`NEXT_PUBLIC_API_BASE` per
  environment, its origin in `KHADBOT_WEB_ORIGINS`, `NEXT_PUBLIC_SENTRY_DSN` once a tracker is chosen, with
  `NEXT_PUBLIC_KHADBOT_ENV` and `NEXT_PUBLIC_KHADBOT_VERSION` matching the API's), and a
  Content-Security-Policy with the hosts in `docs/game-art.md` (the Wowhead hosts and `render.worldofwarcraft.com`
  for `img-src`) and the error tracker's ingest host in `connect-src`.
- **"So busy nights mean less waiting"** is left out of the sign-in copy until WCL's user points are on; restore it
  then.
- **Fonts cover Latin only.** Add `latin-ext` and `cyrillic` subsets if players notice.
- **The two e2e suites share port 3000.** Let `main.py serve` allow another web port if both must run together.
- **The long-pull e2e tests don't run in CI** (`H7z1fqDXjRTFnbtg:15` is 126 MB of LFS). Fetch it in CI or record a
  smaller pull with deaths past the cutoff.
- **The pull rail lists only loaded pulls** until the night overview exists.
- **Tables are tight at 1024 px** beside the 380 px panel; narrow or overlay the panel below about 1200 px.
- **Icons are neutral squares**: no allowed source serves a spell image for a mechanic's `icon_ability_id`
  (`docs/game-art.md`); window markers lack an ability ID; cooldown initials overlap on long pulls; no range zoom.
- **Mechanic panel gaps**: no strip of the pull; aura and cast rules' failures are in no cluster (needs
  `failure_rows`); `MechanicCluster` has no ref to its first hit; timeline mechanic markers carry no `blamed` or
  `blame_state`, so their card can't say who was charged.
- **Crowded deaths overlap** for the pointer; a stacked or clustered death lane would fix it.

## Dev loop

- **`dev:infra` can fail on its first try.** `create_bucket` calls `.get` on `exc.response`, which is None while
  SeaweedFS starts; use `getattr(exc, "response", None) or {}` and retry.
- **WCL's real missing-report answer isn't recorded**, so replay's wording may differ from WCL's (ask before the
  live call).
- **A worker start costs about one WCL point** with real credentials (budget sync).
- **Scripts leave the Postgres pool open** (`scripts/dev/queue_smoke.py`), printing `PythonFinalizationError`.
- **Test-user ID collisions aren't detected** (a hash into `-1` to `-(2**40)`); a check in `issue_session` would.
- **WCL replay limits.** The point counter is per process; a short reset period confuses the budget until its next
  sync; the live report hides fights only in metadata; a stock worker can't replay SYNTH01; replay never exercises
  a registry change; `deploy/stack.env` has no replay settings.
- **E2E reports turn public after five minutes** when the cached "not readable" mark expires; let a test session's
  mark last its `--hours`.
- **Not verified:** a real WCL sign-in through the dev loop; `dev:smoke` in CI.
- **The fake model provider isn't refused on a real deployment**, as WCL replay is: `LLM_PROVIDER=fake` on a
  non-localhost hosted API or worker only logs a warning.

## Security (code review, 2026-11)

The review's high and medium findings are fixed (D91 to D96). Open, most severe first:

- **Medium. The rate limit counts the proxy.** Set `FORWARDED_ALLOW_IPS` with the staging deploy; uvicorn has no
  `limit_concurrency`, and each API process counts on its own.
- **Low.** "Sign out everywhere" is not on the canvas, has no confirmation and no e2e test.
- **Low.** The API's own 429 shows as "WarcraftLogs is busy"; other screens show a generic error for 429 and 413.
- **Low.** Visitor ingests may name fights nobody checked and never expire; refuse unlisted fights and expire
  after an hour.
- **Low.** An event stream checks access once; recheck on the keepalive.
- **Low.** One watch per report held by whoever asks first, no per-user cap; let later watchers join.
- **Low.** Search writes a blob per name asked for, "not found" included, kept for good.
- **Low.** No dependency audit in CI (`pip-audit` over `uv export`, `pnpm audit`).
- **Low.** Workflow actions and base images are pinned by tag; pin SHAs and digests with the deploy pipeline.
- **Low.** `main.py test-session --hours` accepts any number and fails above 168 with the library's message.
- **Low.** `visitor_queue_full` and `rate_limited` reuse `BudgetWaitBody`; split it at the next contract change.
- **Low.** The "not plain-HTTP localhost" interlocks have run only in unit tests; confirm on staging.
- **Low, suspected.** Record builds hold one API-wide lock; include it in the staging load test.
- **Low.** `main.py serve` has no `Host` check (DNS rebinding); add `TrustedHostMiddleware`.
- **Deferred to staging:** TLS and HSTS, CSP, production origins and redirect URI, cookies across web and API
  hosts, platform secrets, the container's user and permissions, proxy settings for event streams, Postgres roles,
  and a real WCL sign-in end to end.

## Agent

- **Agent tools over cached data.** New tools go in `agent/tools/` and read cached data (D53); each needs its
  pipeline first. Bring back the tool routing and tool-error integration tests with them.
- **Parked tests.** `legacy_agent` tests (`tests/integration/cli/test_callback_handler_wired.py`, two LangSmith
  footer tests in `test_repl_loop.py`) are skipped; fix or delete them in the agent refactor and drop the marker.
