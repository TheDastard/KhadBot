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

### Failure rules

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
- **Soul Constrictor** soakers are an undetected failure. The `hit_while_aura` rule exists, but the logs don't
  show who soaked while debuffed: Soul Constrictor (1300685) is never reapplied to a player who has it (0 of 2,400
  applications over 209 Coils), every player hit takes the same amount, and the share debuffed per Coils ranges
  from 0 to 95% (`docs/decisions.md`, "Stack and aura failure rules"). It needs another signal, such as positions
  at the impact, or a soak assignment from a raid plan.
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
