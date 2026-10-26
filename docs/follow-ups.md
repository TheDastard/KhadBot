# KhadBot follow-ups

Open work that is not a task in the plan yet: gaps found while building, ideas worth a later pass, and checks
still owed. Decisions and their reasons live in `docs/decisions.md`; this file only tracks what is left to do.

Add an item when you find a gap you are not fixing now. Remove it (or move it to **Resolved** with a date) in the
commit that deals with it. Encounter-specific items go under that encounter's heading, which the encounter
file's header comment points to.

## Encounter system

The current track is the hosted service: hosted backend first, then the web dashboard and the agents on the web.
Work plan: https://claude.ai/artifact/KaomaNz2SSroTk45DSy9zv (decisions under "Hosted architecture" in
`docs/decisions.md`).

### Data and ingest

- **Scripted cooldowns the data can't classify.** `defensive_cooldowns.yaml` now holds 61 of the 64 defensive and
  healing candidates from build 12.1.5.70448 plus Aura Mastery; Power Infusion moved to the offensive file and
  Innervate to the utility file. The review file's `scripted` list (63 spells) still needs a person: defensive
  ones such as Survival Instincts, Lichborne, Healing Tide Totem, Vampiric Embrace, Stasis (370537) and Time
  Spiral.
- **Offensive cooldowns still open.** `offensive_cooldowns.yaml` holds 40 entries for build 12.1.5.70448:
  the 44 candidates, which now include spell-modifier auras, minus four already in the defensive file
  (Vengeance's Metamorphosis, Invoke Chi-Ji, Invoke Niuzao, Aura Mastery) and those left out below, plus
  Pillar of Frost, Dark Transformation, Breath of Sindragosa, Convoke the Spirits, Zenith and the three
  warlock summons, entered by hand from their spell data. Still open:
  - Damage abilities and enemy debuffs on long cooldowns, which the file doesn't cover (it holds buffs):
    Frostwyrm's Fury, The Hunt, Fury of Elune, Execution Sentence, Divine Toll, Touch of the Magi, Reaper's
    Mark, Touch of Death. Decide whether the file should take them.
  - Candidates left out: Nature's Swiftness (132158), Invoke Yu'lon (322118), Avenging Crusader (216331) and
    Apotheosis (200183) are healing cooldowns, so they belong in the defensive file if anywhere. Incarnation:
    Guardian of Ursoc (102558) also raises maximum health 30% (`A_INCREASE_HEALTH_PCT`, which the defensive
    heuristic doesn't read), so like Sentinel it may belong in the defensive file. Heart of the Wild (1261867)
    is a scripted cast whose same-name auras are older 45 s variants on a 300 s cooldown, so its duration is
    unknown. Sentinel stays defensive.
  - Bladestorm: the logs show casts of 446035, which SimC doesn't list as a player spell; its player spell
    227847 is not a candidate.
  - Racials (Berserking 26297, Blood Fury 20572 and 33702, Ancestral Call 274738) and potions are cast every
    pull, but the file is per class. Decide whether they belong in it, with which `class`.
  - SimC's class, spec and talent tables for this build have no player spell for Icy Veins, Storm, Earth, and
    Fire, Coordinated Assault, Spearhead, Call of the Wild, Apocalypse, Summon Gargoyle, Feral Spirit, Dark
    Ascension, Void Eruption, Shadowfiend, Mindbender, Crusade, Fire and Storm Elemental, Cold Blood,
    Grimoire: Felguard, Warbreaker or Thunderous Roar, and none was cast in the recorded nights. Several names
    exist only as passive talents. Confirm in game that they are gone or passive in this expansion.
  - Spell modifiers are read without knowing which spells they change, so the heuristic can't tell a damage
    modifier from a healing one. Reading spell class flags and labels would separate them.
- **Divine Protection's logged cast ID.** The recorded nights cast Divine Protection as 403876 (161 casts),
  never as the defensive file's 498, so those uses aren't counted. Check which spec casts each and list the
  logged ID, as `offensive_cooldowns.yaml` does for Metamorphosis (200166).
- **Offensive cooldown analysis.** The digest lists offensive casts only. Not built: usage efficiency (casts
  against possible casts, which needs verified cooldowns), alignment with burst phases or damage windows, and
  casts wasted during intermissions.
- **Utility cooldowns the SimC data can't find.** A person must add these to `utility_cooldowns.yaml`: the
  battle resurrections (Rebirth and Intercession lost their resurrect effect; Raise Ally shows only as a
  defensive support candidate and Soulstone as scripted), Mana Tide Totem and Symbol of Hope (not in the
  tables), Wind Rush Totem (its speed aura is missing), Time Spiral and Rescue.
- **Utility auras aren't fetched.** Utility casts come from the unfiltered friendly Casts request; no request
  fetches their auras. Ally-side auras (speed, freedom) would need a third friendly Buffs filter, and enemy-side
  ones (stuns, roots, slows) a Debuffs request with `hostilityType: Enemies`, which no fixture has recorded, so
  its page count and point cost are unknown. Nothing reads utility auras yet; add them when an analysis needs
  durations or who was stunned, after measuring the Enemies Debuffs cost on one long pull.
- **The analyst can't reach most cooldown casts.** A 10-minute pull casts about 150 defensive, 100 offensive
  and up to 70 utility cooldowns. The digest shows 30, 15 and 1, and its defensive list stops near 190 s.
  `cooldown_timeline` returns at most 25 rows (4,000 characters) a call and filters by `phase` only, so most
  casts are out of reach. Decide between (A) exposing the full stream (filters on category, spell, caster and
  time, plus paging) and (B) filtering the noise (per-spell summary records with their own ref, casts near
  deaths, damage windows and phase changes), or both. Keep agent context tight; the dashboard reads
  `PullRecords.cooldowns`, which is uncapped, and should show as much or as little as the reader asks for.
  Stream sizes, constraints and open questions:
  https://claude.ai/code/artifact/5a2d02ed-c264-4e0a-8f2d-a79e0f86fff3
- **Friendly damage the data found but nobody has classed.** The friendly-damage review file lists seven class
  abilities with damage on the caster or allies whose class (redistribution, delayed boss, self-inflicted,
  involuntary) isn't clear from the data: Price of Progress (1233429), Guardian's Hide (1272097), Fiery Rush
  (365199), Blessing of Dawn (210380), Shadow Mend (186439), Void Leech (451963) and Killing Spree (424564).
  Class each from a log or in game, or leave it out. Stagger, Roar of Sacrifice and Cauterize were added with
  proposed classes, unverified.
- **WCL's limit on filter length.** The defensive Buffs filter now holds 95 IDs (under 1 KB); offensive
  cooldowns have their own Buffs request and filter. WCL's limit on filter length is not known.
- **Fetch with the user's WCL token.** Once KhadBot is hosted, the app's 3,600 points an hour cover about ten
  25-pull nights (about five on WCL's new 1,000-point limit), and raid nights cluster on the same evenings.
  WoWAnalyzer's server sends a signed-in user's requests to `/api/v2/user` with that user's token and uses the app
  token only for visitors who aren't signed in. Steps:
  1. Whose budget a user-token request spends: answered by WCL's point-type documentation (`docs/wcl-api.md`,
     "Rate limits and point types"). On the current scheme every request spends the app's pool, which both probe runs
     showed. On the new scheme a signed-in user's requests to user-eligible fields (report events and metadata)
     spend that user's small pool first, then fall back to the app's. Still open: the pool's size, how
     `rateLimitData` reports it, and whether every field ingest uses is labelled user-eligible. A probe on the new
     scheme settles them.
  2. Done: `ingest_report` and `fetch_pull` take a `token_provider` and send every request to `/api/v2/user` with
     it (retrying once on 401); report metadata records `khadbotFetchedWith: "user" | "app"`. With no provider,
     ingest uses the app token as before.
  3. Built, not yet wired into routes: `auth.access.ReportAccess.can_read` decides per user whether a report is
     readable (their token, or the app token for public reports) and caches it in `report_access`; jobs fetch on
     the user's token through `jobs.UserTokenResolver`. Still open: the hosted API must call `require_readable`
     on every report and pull route (including evidence refs and job streams) and mount the sign-in routes with
     CORS that allows credentials, `POST` and the `X-KhadBot-CSRF` header for the web origins.
- **Sign-in details to confirm on the first hosted sign-in.** The refresh grant and the `currentUser` query are
  from WCL's documentation, not a recorded response (`docs/wcl-api.md`, Access); whether a refresh rotates the
  refresh token, and the error text for a private or missing report, are unknown. An ingest job retried after a
  401 re-reads the stored token rather than forcing a refresh, so a token WCL revokes before its stated expiry
  fails that job until its expiry passes or the user signs in again. Expired `sessions` and `oauth_states` rows
  are deleted only when met (a lookup or the next sign-in); a periodic cleanup belongs with the retention job.
  `WclTokens.rotate_keys` re-encrypts stored tokens after a new key is added, but no command runs it yet.
- **Job queue gaps.** (1) A worker that dies without shutting down (killed, out of memory) leaves its job rows
  `running` and the procrastinate job `doing`: nothing marks them failed or retries them. procrastinate records
  worker heartbeats and can list stalled jobs (`JobManager.get_stalled_jobs`); a periodic task should fail the
  `jobs` row and requeue the job. (2) `jobs` rows are never deleted; retention
  should prune finished ones and procrastinate's (`builtin:remove_old_jobs`). (3) No test runs a fixture report through the
  compose stack's worker, because the worker uses the configured filters and encounter files and SYNTH01 needs its
  own; the stack smoke test checks a typed failure instead, and the hosted tests ingest SYNTH01 through a worker with
  its stores injected. Letting a test point the worker at fixture filters and an encounter directory would close it.
- **Live watch gaps.** The watch (`jobs/watch.py`, decision D64) is built; what it doesn't do yet:
  - The points per poll are not measured. The estimate is 1 point (`KHADBOT_WATCH_POLL_POINTS`), from the
    documented cost of a small query; the metadata query also lists every actor and ability, so it may cost more.
    `uv run python scripts/wcl/measure_watch_poll.py --code <public report> --polls 3` measures it (about 20 points;
    it asks for a typed `yes` first). Record the result in `docs/wcl-api.md` and set the default.
  - Whether WCL lists a fight that is still in progress is unverified. The watch ignores a fight whose end is not
    after its start; if WCL lists a running fight with an end time in the future, its pull would be fetched
    early and its raw pages cached as complete. Check on a live report, and if so also wait for a poll in which the
    fight's end is unchanged.
  - A watch ends when its user's sign-in does; it does not fall back to the app token, so a public report of a
    user who signed out stops being followed. Notifying the user (the web app's live screen reads the watch)
    is left to that screen.
  - A watch started on a report that already has fights ingests all of them at once, as live. A long night
    linked late therefore spends its points from the headroom. Linking could skip fights that ended before
    the watch started if that proves a problem.
- **WCL budget gaps.** The budget manager (`jobs/budget.py`, `jobs/budget_store.py`, decision D61) is built;
  what it doesn't do yet:
  - A job's measured spend is the change in `pointsSpentThisHour` across its run, so with two jobs fetching at once
    each is charged the other's points too, and the allowances err high. The bucket itself is corrected by the
    `rateLimitData` read after every job. Per-request costs (WCL reports none per response) would fix it.
  - A failed job is charged its whole estimate, even one that failed before any request (a missing sign-in).
  - A waiting job that is cancelled keeps its place in the budget's line until it is reached (its grant is then
    released) or a day passes, so it can lengthen other jobs' ETAs meanwhile.
  - The per-guild allowance and round-robin need the report's WCL guild, which isn't fetched yet (see "Report guild
    and visibility are not fetched"); until then they fall back to the requesting user, else the report.
  - On WCL's new limits, the user pool's size and whether `rateLimitData` reports user and database points are
    unknown. Once measured, set `KHADBOT_BUDGET_USER_POINTS_PER_HOUR` and turn
    `KHADBOT_BUDGET_USER_TOKENS_SPEND_APP` off, or read the pool from `rateLimitData` if it reports it.
- **Choose a WCL limit scheme and tier.** KhadBot's client is on WCL's current limit (3,600 points an hour); the
  new scheme gives 1,000 application points and 250 database points an hour plus each signed-in user's own pool,
  and new keys start on it, so the current limit may not last. A WCL subscription on the account that owns the
  client raises the limits: Gold 3x the API points ($5 a month), Platinum 9x ($10). Decide when to opt in, and
  measure a night's ingest on the new scheme (application, database and user points) before sizing allowances.
  - Estimate until then: a 25-pull night was about 350 points before cross-fight batching, which saved 172 on the
    measured night, so about 180 now: about twenty nights an hour on 3,600 points, about five on 1,000.
  - Ingest reads `reportData.report` metadata (title, zone, `masterData`, `fights`, `phases`) and `events`, which
    WCL's documentation counts as user-eligible report data, and no rankings or report filtering. Check the
    schema's label on each of those fields, and on `rateLimitData`, the readability check and `currentUser`.
  - The probe to run after opting in: the budget probe signed in with an account that doesn't own the client
    (sign out of Warcraft Logs in the browser first), to see the user pool's size and what `rateLimitData`
    reports with each token.
- **WCL approval for commercial use.** Commercialising an app that uses database-point fields needs WCL's
  explicit approval. Ingest uses none as far as WCL's documentation shows (no rankings or report filtering);
  confirm against the schema's field labels, and ask WCL before any paid tier, together with the questions on
  storing and serving derived data.
- **Merge fetch filters per pull.** Ingest sends one request per fetch spec, about ten per pull, several with no
  filter. Measured 2026-10-20 on `trMgApZCVYkv3jdq:26` (`docs/wcl-api.md`, "Merged-filter results"):
  - One request per hostility with no `dataType` is refused (internal server error), so that merge is out.
  - Merging same-type requests (the two Buffs requests as one, ability IDs unioned) gave identical normalized
    frames but saved 14% of points (21 against 24.31), under the 15% bar, and 58% of wall time on one run that
    followed the baseline fetch of the same pull, so WCL-side caching may explain part of it.
  - Still open before building a merged fetch: repeat the `datatype` run several times with a cold cache and
    see whether the time saving holds. Combining friendly and enemy Casts needs `hostilityType` left out, whose
    behaviour is unknown; a merged filter is also longer, so it runs into WCL's unknown limit on filter length
    (above).
  - A merged request needs its pages' data type taken from the rows, and normalize, the truncation anomaly and
    the raw cache's `_complete` markers are per spec, with the filter hash in the cache path.
- **Filter Healing to fewer row types.** Healing is the largest spec. `type = "heal"` saved 2 of 10 points on
  `trMgApZCVYkv3jdq:26` (2026-10-20, `docs/wcl-api.md`, "Cross-fight and heal-only results"), but Healing carries
  rows other code reads (`docs/wcl-api.md`, "Events"). Rebuilding the seven recorded fixture pulls' records
  without non-heal Healing rows (2026-10-24) cost friendly damage its whole `redistribution` class (30 to 40% of
  friendly damage on four pulls), took a Tempered in Battle top hit and 6 to 8% of window damage from two deaths
  on `QkVa6GHRK8yfrTbw:14`, lost 73 position samples, and dropped Anti-Magic Shell from two deaths' active
  defensives on `H7z1fqDXjRTFnbtg:20`. That last one is a fixture artifact: the recorded Buffs filter doesn't
  request 48707, and live ingest does. Healing stays unfiltered for now. The candidate is
  `type in ("heal", "damage")`, which kept every record and position sample apart from that fixture case, and
  gives up only `absorbed` and `removebuff` rows: the analyst could no longer name the shield that absorbed a hit,
  though DamageTaken still gives the absorbed amount. Before setting it in `build_filter`:
  - Probe it live with `scripts/compare_merged_filters.py --variants healonly --heal-filter`, for the `in (...)`
    form and its point cost.
  - Pin recorded ingest to unfiltered Healing, as it pins the Buffs filter (`RECORDED_BUFF_IDS`), or the new
    filter hash sends it to a Healing directory the fixtures don't have.
  - Check a live pull that an absorb-shield defensive such as Anti-Magic Shell still shows as active from Buffs
    rows alone.

- **The Coiled Altar's player-on-player damage.** 29M over its 7 recorded pulls comes from players' and pets'
  ordinary spells hitting other players, which looks like mind control. It is listed as unclassified friendly
  damage, not registered. When that encounter gets a file, name the mechanic and decide how to tag its damage.
- **Re-ingest local nights to add positions.** Nights ingested before the position table have none, so their
  blame context reads `no_position`. Re-ingest each one offline from the raw page cache:
  `python main.py ingest <code> --offline` for `H7z1fqDXjRTFnbtg`, `QkVa6GHRK8yfrTbw` and `trMgApZCVYkv3jdq`.
  It rewrites all 62 pulls with `events_hash` unchanged (43 s on a copy of the data directory).
- **Other uses of positions.** The position table and lookup helper exist (`analysis/positions.py`); only
  per_cluster blame uses them so far. Not built yet:
  - who stood in whose Volatile Purge eruption (about 5 yards);
  - who stood in a Serpent's Bite soak (about 7 yards of a bitten player) or at a Spectral Coil (about 30
    yards); the debuffs already mark these players, so positions would only show where they stood;
  - an analyst query tool for positions, which changes the prompt version;
  - positions in the dashboard.
- **Positions on encounters that change maps.** Blame and `distance_yd` handle samples on several maps, but no
  recorded pull has any. When an encounter that moves players to another floor or area is recorded, check that
  its `mapID` changes at the move (rather than staying put while coordinates jump), and how often NPCs are
  sampled after one. Anything built on positions over time (movement, formation, tracks) must split a unit's
  samples where `map_id` changes. Blame also assumes an NPC stays where it was last sampled on its map, which
  fails for a boss that relocates within one map between samples.
- **Formation checks from positions.** Positions are stored, so they can judge where the raid stood as a
  whole, not just single mechanics:
  - Who wasn't stacked when a mechanic was baited: each player's distance from the group's centre, or from the
    stack point, at the moment the mechanic is aimed. This needs no raid plan.
  - How the raid split, how many groups there were and who was in each.
  - Who wasn't with their assigned subgroup. This needs raid-plan assignments (plan section 14).
  Hit rows give exact positions; other samples are 0.1 s old at the median and 0.5 s at p95
  (`docs/wcl-api.md`, "Position data").
- **Wrong-target damage.** Show damage on the wrong target, such as hitting Ula'tek instead of the Venomous
  Heart during Rage of the Shackled, or ignoring a priority add. Design settled (`docs/decisions.md`, "Log
  investigations"; proposed contract text in `docs/plan-changes.md` under `encounter/spike-logs`): fetch
  friendly DamageDone for the amounts and not the Enemies side of Threat; add a `kill_targets` block to encounter
  files (spans from a damage window, a phase or an NPC's lifetime), an `off_target_share_gt {value}` failure rule
  and a `KILL TARGETS` digest section. Steps:
  1. With WCL credentials, check DamageDone on one long recorded pull: the event fields (`targetInstance`,
     `amount`, `absorbed`), pages, size and points. Record them in `docs/wcl-api.md`.
  2. Add the fetch spec and normalize it (damage rows with a friendly source and an NPC target).
  3. Add the encounter-file block, the rule, the digest section and a `kill:<id>` ref.
  4. Author the Ula'tek entry: the Venomous Heart (game ID 267460) during `rage_of_the_shackled`.
- **The dashboard API ignores hosted mode.** `create_app` builds a `SqliteFindingsStore` under the data directory
  unless a store is passed, so a hosted deployment must pass `wiring.findings_store()` itself. The hosted API
  should do that, and own the pool's lifecycle (`close_pool` on shutdown).
- **The CLI's `/review` does not use `ReviewPull`.** `/review` runs `ProcessPull` per pull and then the whole
  review graph, because the answer needs the cross-pull synthesis that `ReviewPull` leaves out. Reviewing each pull
  with `ReviewPull` first would call the model twice for a pull whose analysis is partial (those are not cached).
  The queue runs `ReviewPull` per pull and the synthesis afterwards; give the graph a way to synthesize from
  findings it is handed if `/review` should report per-pull progress too.
- **The API doesn't read the read models yet.** `night.SqlSource` serves the report detail and night summary
  from the read-model tables, but `create_app` uses it only when a caller passes `read_source`; the hosted API
  should pass `SqlSource(get_pool())` (`docs/encounter-pipeline.md`, stage 9).
- **Read-model query plans not yet captured.** The plans of the report, night and progression queries have not
  been read on a seeded database; `pytest tests/integration/hosted -m hosted -s -k query_plans` prints them. Check
  that progression uses `pulls_encounter` once a database holds more than a few nights.
- **Read-model equality is checked in CI on the synthetic report only.** The CI `hosted` job checks out without
  Git LFS, so the SQL-against-records equality and progression tests skip the recorded reports there (the
  multi-group report included). Run the hosted tests locally with the LFS pages, or give the job LFS.
- **Report guild and visibility are not fetched.** `reports.guild_id`, `guild_name` and `visibility` stay empty:
  the report query doesn't ask WCL for them. Add them to the report query (checking the field names against the
  WCL schema, `docs/wcl-api.md`) and fill them in `read_models.report_row`; the WCL budget's per-guild allowance
  needs the guild.
- **Read-model rows of removed pulls stay.** The writer replaces one pull's rows at a time; a fight that drops
  out of a report on re-ingest, or a deleted report, keeps its rows until something deletes them (retention).
- **Retention gaps.** The records cleanup, the bucket rules and the backfill cover blobs only. Still open: the
  expired `sessions` and `oauth_states` rows, finished `jobs` rows and procrastinate's finished jobs
  (`builtin:remove_old_jobs`), and the read-model rows above. After `raw/` expires, `ingest --offline` and
  `--refresh-meta` of that report can no longer run; only a fetch from WCL (which spends points) rebuilds events. The
  lifecycle script and the cleanup have run only against moto and unit fakes: run the script against R2 on staging.
  A backfill selects every projected pull of the encounter, including pulls that are already current (processing
  them writes nothing); selecting only stale ones needs the current records hash per pull.
- **Authored files are re-parsed on every records lookup.** `EncounterData.records` reads the cooldown files, the
  friendly-damage registry and the encounter file from `config/` each time the in-process cache misses (about
  115 ms for the registries, tens of ms for the encounter file), which is most of a stored read (about 190 ms a
  pull against about 40 ms with them preloaded). Cache them by file mtime or content hash without losing "an
  edit rebuilds on next use".
- **A stored read still loads the annotated frame.** `RecordsStore.get` returns `PullRecords` with the frame read
  through the annotation cache (re-annotating if it is missing), even for a caller that wants only the digest or
  deaths. Make `PullRecords.annotated` lazy if a view needs records without frames, before the read-model tables
  do it.

- **No S3 service in CI.** The S3 blob store is covered in CI only by the moto-backed unit tests. The
  `tests/integration/hosted/test_blob_s3.py` suite runs the same contract against a real S3-compatible service
  but skips without `KHADBOT_S3_BUCKET`, and the CI `hosted` job has no S3-compatible service container. Add one (SeaweedFS, as in the local stack) (and the
  bucket, `AWS_*` and `KHADBOT_S3_*` variables) to that job.
- **No way to move a local data directory into a bucket.** Objects written to `raw/` without going through the
  compressing store (a plain upload of `.khadbot_data/raw`) have no `.zst` suffix and are not seen by it. A copy
  command should read every key from the local store and put it through `default_blob_store()`.

### Failure rules

- **Failures charged to no one.** Occurrences with no aggro holder (an untanked boss at a stage start, a tail
  never picked up) and soak failures can only be charged once the raid plan's assignments exist (plan section
  14).

### Process and repo

- **Golden questions need real refs.** `expected_refs_any` can only be written from digests of ingested real
  pulls; the golden questions can use the fixture pulls and the full nights in `KHADBOT_DATA_DIR`.
- **Plan section 7 wording.** The encounter-file task still names the old `config/encounters/<id>.yaml` path;
  section 6.2 has the current layout.

- **No error tracker.** `sentry-sdk` was not added; errors reach the JSON logs only.
### Dashboard API

- **Night summary on large reports in local mode.** `EncounterData` keeps records for 32 pulls, so the night
  summary or the report view of a 47-pull night rebuilds records for most pulls on every request (with the
  annotation cache, about 0.3 s each, behind one lock; with the records store about 0.2 s each, 3.9 s for a
  21-pull night). Hosted mode reads the read-model tables instead (`night.SqlSource`). If local mode is too slow,
  serve it from the same per-pull inputs (`night.PullFacts`) cached by records hash, rather than raising the LRU
  size (records hold the whole annotated frame).
- **Typed evidence records.** `GET /v1/evidence/{ref}` returns `record` as an untyped object. If the context
  panel wants generated types per ref kind, publish one record model per kind (the pull summary, the mechanic
  detail with its cluster rows, the event row) and make the response a union keyed on `kind`.

- Hosted API: `GET /v1/reports` lists a user's reports from cached "readable" access rows without re-checking their age,
  so a report made private later stays in that user's list (title and pull counts only) until they open it again and
  the check runs. Filter on `access_recheck_s` if that matters.
- Hosted API: a failed `rate_limited` job has no `retry_after_s` (the job row keeps no WCL hint; only a job queued for a
  retry shows the backoff left).
- Hosted API: a second user's ingest of a report whose ingest is already running gets 409 `ingest_in_progress`
  rather than following the first user's job.

## Agent

- **Agent tools over cached data.** The coach's live-API tools were removed (decision D53), so the coach has none.
  New tools go in `agent/tools/` and read data offline pipelines have fetched and cached (character profiles,
  report and encounter data, sims, guides), never an external API. Each needs its pipeline and cache first.
  Bring back the coach's tool routing, argument and tool-error integration tests with them.

## Encounters

### Ula'tek (3492), Heroic

File: `config/encounters/53-the_venomous_abyss/3492-ulatek.yaml`. Status: first pass, awaiting review (see the
human task table in `docs/decisions.md`).

- **Review the failure rules.** Final counts over the 47 recorded pulls are in the status row.
- **Spectral Coils as a damage window.** Every player takes it, damage scales with how many soak, and it causes
  many overkill deaths. Only Rage of the Shackled is a window today.
- **Rage of the Shackled `expected_defensive_cooldowns: 1`** is a guess; no guide states how many raid
  cooldowns a channel needs.
- **Poisonous Bite** is a dispel mechanic with no failure rule: no source says how long the stacking DoT may
  stay before it counts as a failure (`debuff_duration_gt {ms}` once known).
- **Soul Constrictor** soakers are an undetected failure. The `hit_while_aura` rule exists, but the logs don't
  show who soaked while debuffed: Soul Constrictor (1300685) is never reapplied to a player who has it (0 of 2,400
  applications over 209 Coils), every player hit takes the same amount, and the share debuffed per Coils ranges
  from 0 to 95% (`docs/decisions.md`, "Stack and aura failure rules"). It needs another signal, such as positions
  at the impact, or a soak assignment from a raid plan.
- **Rage of the Shackled's cast is not in the file.** The channel's cast (1286860) and the Venomous Heart's
  cast (1299526) aren't listed, so the filtered enemy Casts page misses both; the unfiltered page has all 55 of
  each. Decide at the file review whether `rage_of_the_shackled` should list the cast. Check first how a damage
  window treats an enemy cast row. Adding it changes the Casts filter, so enemy Casts are refetched for every
  Ula'tek pull.
- **Hits with no enemy cast.** One Circling Prey (`H7z1fqDXjRTFnbtg:15` at 594.6 s) has no cast, the only one
  of 17, and neither Fury Unleashed ID is ever cast. Write rules that use Ula'tek's casts so a missing cast
  changes only who is charged, never whether the occurrence counts.
- **Mephitic Thrash** has no rule: the logs give players inside and outside the sweep the same impact and DoT
  (unmitigated median 401,660 outside 35 yards, 416,240 inside). Positions are the only signal. Each hit row
  carries the player's position, and Gore Rattle's cast row carries its own. Of 1,922 impacts, 74 were within
  3,500 units (35 yards) of Gore Rattle: 59 on the tail's own tank and 15 on other players over 13 casts.
  Nothing in the logs confirms the radius or whether the sweep is a circle or a cone, so a reviewer must
  confirm both first. Then the rule is a hit within that distance of the caster, sparing its aggro holder.
  Positions are stored now, so only the review is missing.
- **Mother's Wrath (no tank in melee)** has no `reach_yd`. Of its 13 failures over the 47 pulls, 2 targets
  were dead and 11 alive at 18.0 to 75.1 yards from Ula'tek's latest position sample. One living target was
  inside her 21-yard swing reach and three more inside her longest swing (30.3 yards), so distance doesn't
  split them cleanly. A reviewer should judge whether the target's distance from Ula'tek is the right test
  for this punishment, and if so, which reach to use.
- **Ingested Venom** (1313529) is tagged as the Serpent's Bite soak debuff on the raid's belief; confirm.
- **Normal and Mythic** are not covered: every entry lists `heroic` only. Check each entry against logs from
  those difficulties before adding them.
- **Unowned raid punishments**: 2 Unchecked Rages at the start of Stage Three and 3 Rattler Slams before anyone
  picked up the tail have no aggro holder; they need tank assignments from a raid plan.

## Resolved

- 2026-10-16: the log didn't lose the enemy casts before the enrage; the boss stopped casting. On
  `H7z1fqDXjRTFnbtg:15` Ula'tek channels Rage of the Shackled from 573.7 to 593.7 s and casts nothing during any
  channel on record; the enrage at 598.8 s is never cast (`docs/decisions.md`, "Log investigations").
  What's left is under Ula'tek.
- 2026-10-11: the legacy comparison is in `docs/legacy-comparison.md`.
- 2026-10-11: the real fixture pulls have normalized-event and digest goldens.
- 2026-10-11: `Pull.boss_hp_pct` / `PullDigest.boss_hp_pct` added from WCL `bossPercentage`, and the
  `start_ms` / `end_ms` comments fixed.
- 2026-10-11: real fixtures go in Git LFS (`tests/fixtures/encounter/wcl/.gitattributes`) and pre-commit skips them; the
  `refs/original` backups from the history rewrite were deleted; the leftover pre-commit `commit-msg` hook was
  uninstalled.
- 2026-10-11: the recorded fixture pulls were missing the enemy Casts filtered to the Ula'tek file's ability
  IDs, so an offline ingest with the current config missed that data type. They now carry both the unfiltered
  and the filtered pages.
- 2026-10-11: the perf tests ingest only the recorded fixture fights and pass, and the query-tool p95 meets its
  budget (see the event store spike in `docs/decisions.md`).
- 2026-10-16: Git LFS budget. The `check` CI job (`.github/workflows/check.yml`) checks out without LFS
  (`GIT_LFS_SKIP_SMUDGE=1`), so it spends no LFS bandwidth and the tests that need real fixtures skip. The
  `real-fixtures` job downloads them (about 200 MB per run) and only runs when started by hand.
