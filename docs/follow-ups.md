# KhadBot follow-ups

Open work that is not a task in the plan yet: gaps found while building, ideas worth a later pass, and checks
still owed. Decisions and their reasons live in `docs/decisions.md`; this file only tracks what is left to do.

Add an item when you find a gap you are not fixing now. Remove it (or move it to **Resolved** with a date) in the
commit that deals with it. Encounter-specific items go under that encounter's heading, which the encounter
file's header comment points to.

## Encounter system

The next track is the digest dashboard; the conversation agent and everything that needs it are deferred. Work
plan: https://claude.ai/artifact/UwgEKmEL1sh1DM47AyPsUh

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
  25-pull nights, and raid nights cluster on the same evenings. WoWAnalyzer's server sends a signed-in user's
  requests to `/api/v2/user` with that user's token and uses the app token only for visitors who aren't signed
  in. `WarcraftLogsClient.query` already accepts a `user_token`, but encounter ingest never passes one. Steps:
  1. Confirm whose budget a user-token request spends. Read `rateLimitData` with the app token and with a user
     token before and after one ingest, and record the result in `docs/wcl-api.md`. WCL's API docs couldn't
     be read when this was written.
  2. Done: `ingest_report` and `fetch_pull` take a `token_provider` and send every request to `/api/v2/user` with
     it (retrying once on 401); report metadata records `khadbotFetchedWith: "user" | "app"`. Whether the user's
     budget pays is still step 1's question; with no provider, ingest uses the app token as before.
  3. Data fetched with a user token can include private reports. The hosted store must record which users may
     read a report, so cached pages and records never reach a user whose own token can't see it. WoWAnalyzer
     adds the WCL user ID to the cache key of signed-in requests for the same reason.
- **Rate-limit users on the app budget.** Requests without a user token (users not signed in to WCL,
  Discord-only users, background jobs) share the app's hourly points. Not built: a global points bucket kept in
  step with `rateLimitData`, per-user and per-guild allowances within it, and a queue that shows the expected
  wait instead of failing when the bucket is empty. Keep headroom for live ingest of linked guilds' raids.
- **Merge fetch filters per pull.** Ingest sends one request per fetch spec, about ten per pull, several with no
  filter. Each request costs about 2 points plus about 1 per extra page (`docs/wcl-api.md`). Wipefest joined
  every config's filter into one `(a) or (b)` expression and paged through a single request per fight. Measure
  on one long recorded pull whether fewer requests with merged `filterExpression`s cut points and wall time:
  points spent (`rateLimitData` before and after), pages, bytes and time, with the normalized events asserted
  identical. Open points:
  - `dataType` and `hostilityType` are per-request arguments. Check whether a request without `dataType` and a
    filter on event type returns the same rows, and whether friendly and enemy specs can share one request.
  - Normalize, the truncation anomaly and the raw cache's `_complete` markers are per spec, and the cache path
    holds the filter hash. A merged request needs its pages' data type taken from each event's `type`.
  - A merged filter is longer, so it runs into WCL's unknown limit on filter length (above).

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
  unless a store is passed, so a hosted deployment must pass `default_findings_store()` itself. The hosted API
  should do that, and own the pool's lifecycle (`close_pool` on shutdown).
- **Serving analysis to many views.** Dashboards and other readers use `PullRecords` through `EncounterData`,
  which rebuilds records per process and keeps a few in memory. Investigate how to serve many views and users at
  scale, for example persisting serialized records keyed by their `produced_from` hashes. A proposed hosted
  design (shared storage, job queue, read models, WCL budget): https://claude.ai/artifact/PGYBvboaxK93jt6fo6eU3B

### Failure rules

- **Failures charged to no one.** Occurrences with no aggro holder (an untanked boss at a stage start, a tail
  never picked up) and soak failures can only be charged once the raid plan's assignments exist (plan section
  14).

### Process and repo

- **Golden questions need real refs.** `expected_refs_any` can only be written from digests of ingested real
  pulls; the golden questions can use the fixture pulls and the full nights in `KHADBOT_DATA_DIR`.
- **Plan section 7 wording.** The encounter-file task still names the old `config/encounters/<id>.yaml` path;
  section 6.2 has the current layout.

### Dashboard API

- **Night summary on large reports.** `EncounterData` keeps records for 32 pulls, so the night summary or the
  report view of a 47-pull night rebuilds records for most pulls on every request (with the annotation cache,
  about 0.3 s each, behind one lock). Measure on a full night and, if it is too slow, cache the per-pull
  aggregates the night needs keyed by `events_hash` and `encfile_hash`, rather than raising the LRU size (records
  hold the whole annotated frame).
- **Typed evidence records.** `GET /v1/evidence/{ref}` returns `record` as an untyped object. If the context
  panel wants generated types per ref kind, publish one record model per kind (the pull summary, the mechanic
  detail with its cluster rows, the event row) and make the response a union keyed on `kind`.

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
