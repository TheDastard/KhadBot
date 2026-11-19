# KhadBot follow-ups

Open work that is not a plan task yet. One item per gap, deleted in the commit that fixes it or decides against
it (`.claude/rules/docs.md`). Encounter-specific items go under that encounter's heading, which the encounter
file's header comment points to. Decisions and their reasons are in `docs/decisions.md`.

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
- **Usage ledger gaps.** A failed ingest writes no WCL row. No job calls `record_llm_usage` yet. The default
  `ANTHROPIC_MODEL` (`claude-sonnet-4-20250514`) has no price in `config/pricing/models.yaml`. Nothing warns when
  prices go stale.
- **No error tracker**; errors reach the JSON logs only.
- **Worker health is per machine.** `main.py worker --check` reads a local file; a heartbeat row would show other
  machines.

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
  environment, its origin in `KHADBOT_WEB_ORIGINS`), and a Content-Security-Policy with the hosts in
  `docs/game-art.md` (the Wowhead hosts and `render.worldofwarcraft.com` for `img-src`).
- **"So busy nights mean less waiting"** is left out of the sign-in copy until WCL's user points are on; restore it
  then.
- **Fonts cover Latin only.** Add `latin-ext` and `cyrillic` subsets if players notice.
- **The two e2e suites share port 3000.** Let `main.py serve` allow another web port if both must run together.
- **The long-pull e2e tests don't run in CI** (`H7z1fqDXjRTFnbtg:15` is 126 MB of LFS). Fetch it in CI or record a
  smaller pull with deaths past the cutoff.
- **The pull rail lists only loaded pulls** until the night overview exists.
- **Tables are tight at 1024 px** beside the 380 px panel; narrow or overlay the panel below about 1200 px.
- **Timeline markers lack spell IDs**, so mechanics and windows show a neutral square and cooldown initials overlap
  on long pulls. Add the ability ID to both markers. The timeline's own range zoom isn't built.
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

## Encounters

### The Venomous Abyss, game art

- **The client export stops short.** Three runs of `exportInterfaceFiles art` on client 12.1.5.70783 wrote every
  journal file up to file ID 7966637 and none of the ten after it, Kith'ix's `ui-ej-boss-kithix.blp` (8214195) among
  them, likely a timeout. New bosses' portraits will be missing until the export completes; find a way to make it
  finish (or export the newest files) before the next raid tier.
- **Hosted buckets.** The portraits are uploaded to the local stack only; staging and production need the upload
  script run against their buckets (`docs/game-art.md`, "Uploading to a hosted bucket"), and the Blizzard client ID
  and secret in their secrets. The upload folder must also hold Kith'ix's `UI-EJ-BOSS-Kithix.blp`, which the export
  lacks.

### Ula'tek (3492), Heroic

`config/encounters/53-the_venomous_abyss/3492-ulatek.yaml`, first pass awaiting review. Over the 47 recorded
pulls: coverage `full`, no `phase_trigger_unmatched`, failures Mother's Wrath 13, Unchecked Rage 21, Rattler Slam
10, Calcified Corpse 19, Circling Prey 12, Desperate Thrash 28, Acidic Expulsion 1.

- **Review the failure rules** against those counts.
- **Spectral Coils as a damage window.** Every player takes it and it causes many overkill deaths; only Rage of
  the Shackled is a window today.
- **Rage of the Shackled `expected_defensive_cooldowns: 1`** is a guess.
- **Rage of the Shackled's cast** (1286860) and the Venomous Heart's (1299526) aren't listed, so the filtered enemy
  Casts page misses them. Decide at review; adding them refetches enemy Casts for every pull.
- **Poisonous Bite** has no rule until a source says how long the DoT may stay (`debuff_duration_gt`).
- **Soul Constrictor soakers** are undetected: the debuff is never reapplied and every hit is equal
  (`docs/encounter/failure-rules.md`). Needs positions at impact or a raid plan.
- **Mephitic Thrash** has no rule; positions could judge it once a reviewer confirms the radius and shape.
- **Mother's Wrath `reach_yd`.** Target distance doesn't split the 13 failures cleanly; decide whether distance is
  the right test.
- **Hits with no enemy cast.** One Circling Prey has no cast; rules using Ula'tek's casts must change only who is
  charged, never whether it counts.
- **Ingested Venom** (1313529) as the Serpent's Bite soak debuff is the raid's belief; confirm.
- **Normal and Mythic** aren't covered; check each entry against those logs first.
- **Unowned punishments.** 2 Unchecked Rages and 3 Rattler Slams have no aggro holder; they need tank assignments.

### The Coiled Altar (3429), Heroic

`config/encounters/53-the_venomous_abyss/3429-the_coiled_altar.yaml`, first pass awaiting review; its comments give
counts over 35 Heroic pulls (one kill).

- **Review the 16 failure rules**, above all Volatile Venom bystanders (701) and Axegrinder (568). `MECHANIC_CAP`
  (8) cuts a failing mechanic from 6 pulls' digests.
- **Player-on-player damage**: 104.8M over the 32 pulls that reach Dreadmarch is unclassified, mostly players' DoTs
  (451963, Frost Fever, Virulent Plague, Flame Shock); none hits a player under Dreadmarch. Name the cause.
- **Guillotine soak threshold** (5, from the guides): Execution follows only soaks of 2 or fewer, and one Grim
  Execution has no soak hit. A soak-group `hit_while_aura` rule waits for logs with a second soak.
- **Gloombomb bystanders** need a grace window on an aura's end before `hit_without_aura` stops charging carriers.
- **Soul Sever** has no rule: Malacrass never melees, so his tank must come from taunts, Dread Bolt or a raid plan.
- **Confirm**: Noxious Ground's free tick, Toxic Deluge as avoidable, Fangs' `expected_defensive_cooldowns: 1`, and
  Unworthy's (1312424) trigger. No source yet for Venomfang, Venom Rupture, Corrupted Toxin, Dreadmarch rules.

### Kith'ix (3513), Heroic

`config/encounters/53-the_venomous_abyss/3513-kithix.yaml`, first pass awaiting review; five Heroic pulls, one kill.

- **The intermission isn't a phase.** Suffocating Darkness repeats and ends on Liadrin's Divine Radiance, not a boss
  event, so it sits inside `p1`.
- **Judgment calls to confirm**: Extinguish when a tank survives it by a planned immunity or defensive, Dark
  Devastation's 8 s window, Digestive Juices' 3-stack limit (a 3-stack set also wiped), Voidscar hits on the tanks
  placing it, and Overwhelming Fear as a full 6 s rather than an early dispel (needs positions).
- **Suffocation double counts** an unhealed Abyssal Grasp (9 of 21). Stage Two rests on one pull: Null Gate rules
  and Nullify's lethal stack (4 to 6 by guide) are unconfirmed; Hatch, Mindsting and Dark Frenzy never appear.
- **Need a source**: Blood of the Old Ones (1308670, 6 killing blows), Nightfall's stack limit, and Eightfold
  Eclipse's `expected_defensive_cooldowns: 1`; Eclipse Fragment soaks need a raid plan.

### Nek'zali the Soulcoiler (3470), Heroic

`config/encounters/53-the_venomous_abyss/3470-nekzali_the_soulcoiler.yaml`, first pass awaiting review; seven Heroic
pulls, three kills.

- **Review the failure rules**; Latent Cultist (299 contacts) looks high: is one tick a failure?
- **Log-derived limits to confirm**: Ritual Burn past 5 (a six-Invoke Stage Two would pass it with no Amani in the
  well), Hollowing Strikes past 15 (guides swap at 6 to 8), Hungering Pyre under 6 soakers (should scale with size).
- **A Restless Amani reaching the well** has no rule: it logs only a Soulcoil Rite (1288772). It needs a rule that
  counts occurrences not preceded by a listed cast, charged to no one; `ritual_burn` catches only pile-ups.
- **Possession Barrage** early bursts need positions (its defensive count is a guess); **Soulcoil Well** counts
  include raids walking in to end a lost pull, which the wipe cutoff doesn't always catch.
- **Confirm** the intermission starting on Ritual of Awakening (23 s before WCL's transition). No rule yet for Latent
  Cultist emergence, Essence Rend placement (needs positions) or Corpse Blight stacks.

### Entombed Sentinels (3445), Heroic

`config/encounters/53-the_venomous_abyss/3445-entombed_sentinels.yaml`, first pass awaiting review. Failures: Blood
Venom 549, Living Venom 287, Deadly Venom 60, Cultivated Burst 22, Unstable Miasma 9, Noxious Blast 9, Injection 2.

- **Review the thresholds**: Blood Venom `hits_per_cluster_gt 1` (is crossing pools part of the fight?) and Unstable
  Miasma `soak_count_lt 8` (from these logs only; other raid sizes need a raid plan's group sizes).
- **Unowned failures.** Noxious Blast needs droplet assignments; no blame option names Injection's debuffed tank.
- **No rule yet** for Toxic Droplets, Clinging Murk, Blighted Blood (guides disagree on dispel timing) and the marks:
  no source gives a limit. Living Venom has no cast; whether stepping on a droplet launches it is unconfirmed.
- **Not in the logs**: Empowering Slam's bonus, Ula'tek's Dominance (testable from positions once the range is
  settled), Helical Toxins' starting stacks. Contaminate's `expected_defensive_cooldowns: 1` is a guess.

### Nymrissa Wavecaller (3379), Heroic

`config/encounters/53-the_venomous_abyss/3379-nymrissa_wavecaller.yaml`, first pass awaiting review, from one Heroic
kill, so no rule is tested yet: recheck on wipes, more kills and the Mythic pulls.

- **Unseen in the kill, so no IDs**: Bubblefin Berserkers and their Pulsing Tides (a murloc reaching the bubble has
  no rule), Shatter, Wavecaller's Might and Unending Tides. Pulsing Tides and Shatter fit `per_cluster` by nobody.
- **No rule yet** for Frost Orb stacks (`debuff_stacks_gt 2` would give 4 failures; is 3 the limit?) and Lingering
  Frost, which needs to excuse the soaker's own patch or a tick count past which standing in it fails.
- **Blame**: Iceblade Flurry past 6 stacks charges no one, though the other tank should have taunted; whether Wild
  Bite on a tank knocked into the water is that tank's error is for a reviewer.
- **Guesses**: Abyssal Rain's and Pop!'s defensive counts, and what Pop!'s zero hit marks. Repeating phases on the
  Alluring Bubble and Swirling Whirlpools casts could split the single phase's ~110 s cycle.

### Vashnik the Malignant (3455), Heroic

`config/encounters/53-the_venomous_abyss/3455-vashnik_the_malignant.yaml`, first pass awaiting review, from three Heroic
kills and no wipes. Failures to review: Plague Froth 177, Plague Wave 221, Stygian Burst 29, the rest under 10.

- **Plague Froth may overcount.** 60 failures are bystanders hit only by the first tick, before anyone could move;
  `hit_without_aura` has no first-tick grace. Decide whether they count. Plague Wave (about 9 players an eruption,
  healed through) may not be a failure at all.
- **Stygian Burst blame.** Carriers in melee hit the tanks repeatedly, so the carrier may be at fault rather than the
  player hit; a former carrier hit within 1.5 s of the infection ending also counts.
- **Staggered dispels and add kills** (Exploding Infection, Burning Venom deaths) need a rule kind for two occurrences
  closer than a limit; Caustic Surge has no stack counts, so `debuff_stacks_gt` can't see the overlap.
- **Unconfirmed**: the unsoaked Catalytic Bile ID (1282616), Virulent Fumes and Congealing Bolt (not in the Dungeon
  Journal), Imbibe's cooldown count; the Blood fountain and leaked adds aren't covered (YAML header).

### The Lost Explorers (3497), Heroic

`config/encounters/53-the_venomous_abyss/3497-the_lost_explorers.yaml`, first pass awaiting review, from only four
Heroic pulls (three kills); counts and the unseen abilities are in its comments.

- **Phases assume the feed order Nema, Epu, Gebbo.** Another order gets wrong phases, so the format needs phases
  that match in any order; no pull reached the fourth Final Ascension, so the last phase is missing.
- **Review**: Shell Spin charges the cast's target (7 of 32 casts); Aftershock `hits_per_cluster_gt 0`, Shredding
  Shards past 7 stacks, Blink Nova's defensive count. Relic Rupture charges no one until a raid plan assigns crates.
- **No rule yet** for Mighty Thud (no failed leap seen, and not a damage window), Steady Strikes, Splinters and
  Spreading Flames; Elemental Explosion's rule is untested on a debuff carried into the next volley.
- **Needs positions or more logs**: Blink Nova placement, United Defense, Fishy Feedback's damage, Haunting Spirits
  (1310561, unexplained deaths in the wipe).
