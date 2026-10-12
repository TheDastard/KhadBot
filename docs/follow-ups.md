# KhadBot follow-ups

Open work that is not a task in the plan yet: gaps found while building, ideas worth a later pass, and checks
still owed. Decisions and their reasons live in `docs/decisions.md`; this file only tracks what is left to do.

Add an item when you find a gap you are not fixing now. Remove it (or move it to **Resolved** with a date) in the
commit that deals with it. Encounter-specific items go under that encounter's heading, which the encounter
file's header comment points to.

## Encounter system

### Data and ingest

- **Real-report contract fields** (plan section 6.1). Add `Pull.boss_hp_pct` and `PullDigest.boss_hp_pct`
  (request `bossPercentage` in `GET_REPORT_META`), take `phase_reached` and the `phase_trigger_unmatched` check
  from the last phase transition instead of `last_phase`, and fix the `start_ms` / `end_ms` comments
  (milliseconds from report start).
- **Death records name the killing ability "Unknown Ability (0)".** Every death in the Heroic Ula'tek digests
  reads that way although the top hits show the real killing blow. WCL death events carry
  `killingAbilityGameID` and their `abilityGameID` may be 0; normalize it, or fall back to the overkill hit at
  the death time.
- **Player self-damage registry.** Friendly-source damage (Spirit Link, Time Dilation, Burning Rush, trinkets)
  counts as unregistered encounter damage today; it is all of the 3.1% left on Ula'tek. Proposed: a shared
  `config/registries/friendly_damage.yaml` classing each ability as redistribution (Spirit Link, Tempered in
  Battle), delayed boss damage (Time Dilation, Stretch Time) or self-inflicted (Burning Rush, Set Fire to the
  Pain, Seriously Sharp Seashell); exclude friendly-source damage from the unregistered share; report
  self-inflicted damage per player; flag it in death windows; list unclassified friendly-source abilities the
  way unregistered boss abilities are listed.
- **Positions.** `x` / `y` / `facing` are already in the raw pages. Keeping them grows parquet by 39% and
  normalize time by about 2 s per night (`docs/wcl-api.md`). They would answer "was the aggro holder in melee
  range" and could make standing-in-it mechanics such as Mephitic Thrash detectable. Waiting on a decision.
- **Wrong-target damage.** The Enemies side of WCL's Threat data records every player and pet action aimed at
  an NPC, per instance, and its `table` view gives bands of who was on which NPC. That would show damage on
  the wrong target, such as hitting Ula'tek instead of the Venomous Heart during Rage of the Shackled, or
  ignoring a priority add. Cost: about 12.7k events and 4.9 MB of raw pages per long pull (2 pages, a few
  points), or 551 kB for the table view. Needs a "kill target" per phase or window in the encounter file and
  DamageDone events for the amounts; the Threat events only say who was attacking.
- **Enemy casts stop before the enrage.** `H7z1fqDXjRTFnbtg:15` has no enemy casts after 574 s of a 618 s pull
  while Fury Unleashed damage continues. Find out whether the boss casts nothing during the enrage or the log
  lost them before relying on enemy casts near the end of a pull.
- **Synthetic fixture newlines.** `tests/fixtures/wcl/build_synth.py` writes JSON without a final newline and
  the pre-commit hook adds one, so regenerating the fixture shows a one-character diff per file. Write the
  newline in the generator.

### Failure rules

- **Stack counts.** No rule can test how many stacks a debuff reached ("never let Acidic Expulsion pass 2
  stacks"). A `debuff_stacks_gt {value}` rule over the `stack` column would cover it.
- **Hit while carrying a debuff.** No rule can say "taking this mechanic while debuffed with X is a failure"
  (soaking Spectral Coils with Soul Constrictor). A rule that joins hits to the target's active auras would.
- **Charging the cause of an add.** Damage from an add that should not have spawned (a hatched Blightscale
  Viper) is counted, but nobody is charged for letting it spawn. Needs a way to link the spawn to the event
  that caused it.
- **Failures charged to no one.** Occurrences with no aggro holder (an untanked boss at a stage start, a tail
  never picked up) and soak failures can only be charged once the raid plan's assignments exist (plan section
  14).

### Process and repo

- **Merge the stacked branches before M5.** `encounter/m0-m1-groundwork-data-layer` → `m2-annotation-digest` →
  `m3-analyst-loop` → `m4-findings-fanout` → `real-wcl-fixtures` are unmerged; `main` has none of the encounter
  work.
- **Exit checks that real fixtures now unblock.** The legacy comparison (M2.6) and digest goldens for the real
  fixture pulls.
- **Golden questions need real refs.** `expected_refs_any` can only be written from digests of ingested real
  pulls; the golden questions can use the fixture pulls and the full nights in `KHADBOT_DATA_DIR`.
- **Git LFS budget.** The real fixtures are about 200 MB. On GitHub's free tier LFS storage and bandwidth are
  each 1 GB (bandwidth per month), so every full clone or CI checkout with LFS spends about 200 MB. Jobs that
  don't need real fixtures can set `GIT_LFS_SKIP_SMUDGE=1`.
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
- **Blightscale Viper hatches** are counted as damage but charged to no one; needs the add-cause rule above.
- **Mephitic Thrash** has no rule: the logs give players inside and outside the sweep the same impact and DoT.
  Positions may make it detectable.
- **Ingested Venom** (1313529) is tagged as the Serpent's Bite soak debuff on the raid's belief; confirm.
- **Normal and Mythic** are not covered: every entry lists `heroic` only. Check each entry against logs from
  those difficulties before adding them.
- **Unowned raid punishments**: 2 Unchecked Rages at the start of Stage Three and 3 Rattler Slams before anyone
  picked up the tail have no aggro holder; they need tank assignments from a raid plan.

## Resolved

- 2026-10-11: real fixtures go in Git LFS (`tests/fixtures/wcl/.gitattributes`) and pre-commit skips them; the
  `refs/original` backups from the history rewrite were deleted; the leftover pre-commit `commit-msg` hook was
  uninstalled.
- 2026-10-11: the recorded fixture pulls were missing the enemy Casts filtered to the Ula'tek file's ability
  IDs, so an offline ingest with the current config missed that data type. They now carry both the unfiltered
  and the filtered pages.
- 2026-10-11: the perf tests ingest only the recorded fixture fights and pass, and the query-tool p95 meets its
  budget (see the event store spike in `docs/decisions.md`).
