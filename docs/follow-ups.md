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

- **Player self-damage registry.** Friendly-source damage (Spirit Link, Time Dilation, Burning Rush, trinkets)
  counts as unregistered encounter damage today; it is all of the 3.1% left on Ula'tek. Proposed: a shared
  `config/registries/friendly_damage.yaml` classing each ability as redistribution (Spirit Link, Tempered in
  Battle), delayed boss damage (Time Dilation, Stretch Time) or self-inflicted (Burning Rush, Set Fire to the
  Pain, Seriously Sharp Seashell); exclude friendly-source damage from the unregistered share; report
  self-inflicted damage per player; flag it in death windows; list unclassified friendly-source abilities the
  way unregistered boss abilities are listed.
- **Investigate position data as a signal.** Events with `includeResources` carry `x`, `y`, `facing` and
  `mapID` for one unit (`resourceActor`: 1 source, 2 target), on about 73% of rows. They are already in the raw
  pages, so keeping them costs no API points: parquet grows by 39% and normalize by about 2 s per night
  (`docs/wcl-api.md`). What to find out:
  - **Which questions it answers.** Candidates: was the aggro holder in melee range when Unchecked Rage or
    Rattler Slam fired (separates a tank out of position from a tank who never got aggro); who stood inside
    Mephitic Thrash's 35 yards or Circling Prey's 13 yards; who failed to spread for Volatile Purge; who stood
    in a soak.
  - **Whether positions are fresh enough.** A unit's position only updates on events where it is the resource
    actor. Measure how stale the latest position is at the moments that matter, per role (tanks, melee,
    ranged, the boss).
  - **The distance scale.** The units are not documented. Calibrate from known events instead of assuming a
    scale: boss-to-target distance at Ula'tek's melee swings was 1,336 to 2,026 units (5th to 95th percentile)
    on the recorded pulls; journal radii (13 and 35 yards) give two more anchors if hits inside and outside them
    can be told apart.
  - **The shape of it.** Columns on every event row, or a separate per-pull position table sampled per actor,
    and a query helper such as "distance between two actors at `t`".
  Decide from that whether to keep positions, for which questions, and in what form. A scratch spike script
  measured the storage and timing; nothing is in the codebase yet.
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

### Failure rules

- **Stack counts.** No rule can test how many stacks a debuff reached ("never let Acidic Expulsion pass 2
  stacks"). A `debuff_stacks_gt {value}` rule over the `stack` column would cover it.
- **Hit while carrying a debuff.** No rule can say "taking this mechanic while debuffed with X is a failure"
  (soaking Spectral Coils with Soul Constrictor). A rule that joins hits to the target's active auras would.
- **Charging the cause of an add.** Damage from an add that should not have spawned (a hatched Blightscale
  Viper) is counted, but nobody is charged for letting it spawn. Adds have no spawn event, but the hatch shows
  as the egg's aura coming off its carrier (`docs/decisions.md`, "Log investigations"). Implement the proposed
  `aura_removed_by {ability_ids, on_death}` rule (`docs/plan-changes.md` under `encounter/spike-logs`). Each
  removal of the mechanic's aura from a player, followed within 200 ms by a hit from `ability_ids` on that
  player, is a failure charged to them. With `on_death: true`, a removal followed within 200 ms by the
  player's death is a failure charged to no one. The failure is on the remove row and belongs to no cluster.
  Validation: `ability_ids` non-empty, `blame` rejected. Bump `ANNOTATION_VERSION`.
- **Failures charged to no one.** Occurrences with no aggro holder (an untanked boss at a stage start, a tail
  never picked up) and soak failures can only be charged once the raid plan's assignments exist (plan section
  14).

### Process and repo

- **Golden questions need real refs.** `expected_refs_any` can only be written from digests of ingested real
  pulls; the golden questions can use the fixture pulls and the full nights in `KHADBOT_DATA_DIR`.
- **Plan section 7 wording.** The encounter-file task still names the old `config/encounters/<id>.yaml` path;
  section 6.2 has the current layout.

## Encounters

### Ula'tek (3492), Heroic

File: `config/encounters/53-the_venomous_abyss/3492-ulatek.yaml`. Status: first pass, awaiting review (see the
human task table in `docs/decisions.md`).

- **Review the failure rules.** Final counts over the 47 recorded pulls are in the status row.
- **Spectral Coils as a damage window.** Every player takes it, damage scales with how many soak, and it causes
  many overkill deaths. Only Rage of the Shackled is a window today.
- **Rage of the Shackled `expected_cooldowns: 1`** is a guess; no guide states how many raid cooldowns a channel
  needs.
- **Poisonous Bite** is a dispel mechanic with no failure rule: no source says how long the stacking DoT may
  stay before it counts as a failure (`debuff_duration_gt {ms}` once known).
- **Acidic Expulsion** should fail past 2 stacks; needs the stack-count rule above.
- **Soul Constrictor** soakers are an undetected failure; needs the hit-while-debuffed rule above.
- **Blightscale Viper hatches** are counted as damage but charged to no one. Once the `aura_removed_by` rule
  above exists, give `malignant_shell` `failure: {rule: aura_removed_by, ability_ids: [1292403], on_death: true}`.
  Over the 47 pulls that charges 29 shells broken by Caustic Waves and counts 125 carrier deaths. Each of the 29
  was followed by a Viper, and 101 of the deaths were. 29 of the 153 Vipers have no shell removal in the 8 s
  before them and stay uncharged: they come in groups at 198 to 262 s and 395 to 468 s, and nothing fetched
  shows their cause.
- **Rage of the Shackled's cast is not in the file.** The channel's cast (1286860) and the Venomous Heart's
  cast (1299526) aren't listed, so the filtered enemy Casts page misses both; the unfiltered page has all 55 of
  each. Decide at the file review whether `rage_of_the_shackled` should list the cast. Check first how a damage
  window treats an enemy cast row. Adding it changes the Casts filter, so enemy Casts are refetched for every
  Ula'tek pull.
- **Hits with no enemy cast.** One Circling Prey (`H7z1fqDXjRTFnbtg:15` at 594.6 s) has no cast, the only one
  of 17, and neither Fury Unleashed ID is ever cast. Write rules that use Ula'tek's casts so a missing cast
  changes only who is charged, never whether the occurrence counts.
- **Mephitic Thrash** has no rule: the logs give players inside and outside the sweep the same impact and DoT.
  The position investigation above may make it detectable (distance from Gore Rattle at the impact).
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
