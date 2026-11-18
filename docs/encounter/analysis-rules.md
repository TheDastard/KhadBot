# Encounter analysis rules

- **SYNTH01 generator fix**: the sim applied damage and heals in script order, so `hitPoints` and deaths did not
  follow the timeline. It now applies them chronologically; deaths match the fixture README (fight 1: 8, 23, 41,
  50 s; fight 2: 55 s). Normalized goldens were regenerated in the same commit.
- **Ability names**: `EventStore` gains `write_abilities` / `abilities(code)` (`events/<report>/abilities.json`,
  from `masterData.abilities`) so death records and unregistered damage can name abilities. Reports ingested before
  M2 have no names until re-ingested.
- **Signatures vs plan section 10**: `annotate(pull, events, enc, actors, *, cutoff_fraction)` (no registry; nothing
  in annotation uses it). `Annotated` carries the `pull`, so analysis functions take no separate `pull`.
  `death_records(a, actors, registry=None, abilities=None)` needs the registry for `active_defensives`.
  `detect_phases` keeps its signature; `unmatched_phase(spans, enc)` names the trigger that stopped detection.
  `build_digest` adds keyword-only `cache`, `cutoff_fraction` and `unregistered_cap`.
- **Window coverage counts `kind: raid` cooldowns only** (plan 6.3 said raid/external). A damage window is raid-wide
  damage; an external on one player does not cover it. Plan section 6.3 updated to match.
- **Hits and clusters**: a "hit" is a `damage` event on a friendly target. Clusters, `window_occurrence`,
  `MechanicStat.hits/damage_taken` and the death window all use hits. Damage figures use WCL `amount` (after
  absorbs and overkill). `not_interrupted` failures are the enemy cast rows, so they have no player in
  `failures_by_player`; `soak_count_lt` failures mark a cluster's first row.
- **`time_to_die_ms`** is None when the actor had no reading at >= 90% HP in that life (not only when HP data is
  missing). A second death's window and time-to-die are clipped to after the previous death (battle res).
- **Anomalies**: `phase_trigger_unmatched:<id>` is raised only when WCL's phase transitions show the pull reached
  that phase (rule since 2026-10-16 below); an early wipe never reaching P2 is not an anomaly.
  `missing_data_type:<T>` checks only DamageTaken, Casts and Healing, which every real pull has; zero Deaths,
  Interrupts or Dispels rows are legitimate. `unregistered_damage:<pct>` is raised only when an encounter file
  exists and the share is >= 10%.
- **Triggers needing data not fetched yet**: `boss_hp_below` needs damage on the boss (no DamageDone spec) and
  `boss_aura_applied` needs auras on the boss (Buffs are fetched for friendlies only). They match nothing on real
  data until S1 adds those specs; prefer `boss_cast`, `wcl_phase` or `time` in H3 until then.
- **CLI**: `/digest [<report-code>] <fight> [--json] [--unregistered]` and `/pulls [<report-code>]` default to the
  last report ingested in the session. `--unregistered` lists up to 20 abilities. Golden files can be rewritten
  with `pytest --update-goldens` (`UPDATE_GOLDEN=1` still works).
- **Offline ingest of SYNTH01 through the CLI** misses the cache, because the CLI derives Buffs / enemy-Casts filters
  from `config/` while the fixture was recorded with synthetic IDs. Tests pass the synthetic IDs explicitly.
- **Encounter file layout and difficulties (2026-10-11)**: files live at
  `config/encounters/<zone_id>-<zone_slug>/<encounter_id>-<encounter_slug>.yaml` (Ula'tek:
  `53-the_venomous_abyss/3492-ulatek.yaml`), so a file says which fight it is without opening it. The zone ID is
  the WCL zone ID from report metadata, the only instance ID the data carries. Slugs are lowercase with apostrophes
  dropped and other runs of non-alphanumerics turned into `_`; a hyphen separates ID from slug because a second dot
  reads as a file extension. The file declares `zone: {id, name}`, and the loader rejects a file whose path doesn't
  match its declared zone, encounter ID and name, and an encounter ID with two files. Every entry (phase, mechanic,
  damage window, ignored ability) has a required `difficulties` list (`lfr`, `normal`, `heroic`, `mythic`; WCL
  codes 1, 3, 4, 5, of which only 4 and 5 are confirmed on recorded reports). `ignore_ability_ids` became
  `ignored_abilities: [{ability_id, name, difficulties}]`. Validation runs per difficulty, so an ID or ability may
  repeat on entries whose difficulties don't overlap (for example a different failure rule on Mythic). Analysis
  uses `load_encounter_view(encounter_id, pull.difficulty)`: only that difficulty's entries, and None (coverage
  `none`) when no phase covers the pull's difficulty or the difficulty is unknown. `encfile_hash` is taken over that
  view, so editing one difficulty's entries leaves the other difficulties' annotations and findings cached. The
  ingest filter still uses every difficulty's ability IDs. Plan section 6.2 updated to match (plan 0.3.4).
- **Failure blame (2026-10-11)**: annotation adds a `blamed_id` column, the player charged with each failure
  row. The existing rules charge the friendly player hit (or the debuffed player for `debuff_duration_gt`);
  `not_interrupted` and `soak_count_lt` charge no one. `MechanicStat.failures_by_player` and
  `MechanicCluster.failed_players` now count `blamed_id`, not the failure row's target, and an `event:` ref on a
  failure row adds `blamed` (a name). Four rules are added so mechanics that punish the raid for one player's
  mistake can be charged to that player:
  - `per_cluster` + `blame`: one failure per cluster, on its first hit. `blame: {by: cast_target, ability_id, ms}`
    charges the friendly target of the latest enemy cast of `ability_id` in the `ms` before;
    `{by: aggro_holder, npc_game_id, ms}` charges the NPC's aggro holder (matched by game ID, since a boss can have
    two actors with one name): the player behind its latest melee swing or taunt in the Threat data in the `ms`
    before (see the Threat entries below). With no match the failure still counts, charged to no one. `blame` is required by `per_cluster` and rejected on every other rule.
  - `hit_amount_gt {value}`: each hit larger than `value` before mitigation (`unmitigated`, else `amount +
    absorbed`), for mechanics that hit everyone but hit hardest those standing in the wrong place.
  - `aura_applied`: each application of one of the mechanic's auras on a player, charged to that player. Like
    `not_interrupted` rows, these are not hits and belong to no cluster.
  - `hits_on_non_tank {ms}`: one failure per player per cluster for players other than the aggro holder of the
    NPC instance that hit them (latest swing or taunt on it in the `ms` before their first hit). Instances matter
    because adds of one kind can share an actor ID (both Weakened Doomscales are one actor), and only the first hit
    is judged because NPCs stop swinging while they cast, so DoT ticks on the tank would otherwise read as failures.
  Annotated frames carry `ANNOTATION_VERSION` (now 3) in their cache sidecar, so frames from before the change
  are rebuilt. The optional `blame` field also changes every encounter file's hash once. Plan sections 6.2
  (rules table) and 6.3 (`failures_by_player`) updated to match (plan 0.3.4).
- **Threat data and NPC instances (2026-10-11)**: ingest fetches a tenth data type, `Threat` (Friendlies side,
  about 1 point per pull; see `docs/wcl/report-data.md`). WCL Threat has no threat values: it is target tracking, NPC melee
  swings (misses included), taunts and NPC deaths. Normalize keeps the NPC swings, the taunt debuffs on NPCs and
  the NPC deaths, and drops the players' taunt casts, which repeat the Casts data. The event schema gains `source_instance` and
  `target_instance` (WCL `sourceInstance` / `targetInstance`), which number NPCs sharing one actor ID; every
  `events_hash` changes, so all pulls re-ingest from the raw cache. The digest gains `aggro`: per boss NPC, its
  melee swings, the top 3 targets with counts and shares, and the taunts that landed by player. It renders as an
  `AGGRO` section under the pull ref, and the `pull` ref resolves with the same `aggro` list so verify can check
  its numbers. Event rows and death top hits name NPCs with their instance (`Blightscale Rawling #51`).
  Recorded fixtures gained Threat pages and the encounter-filtered enemy Casts. Plan sections 6.1 (FetchSpec,
  event schema), 6.4 (digest) and 6.5 (`pull` ref) updated to match (plan 0.3.4). Blame reads it through
  `aggro_events` (see the blame entries).
- **Aggro bands (2026-10-11)**: `analysis/aggro.py` builds per-NPC-instance bands of who each NPC was attacking:
  a band starts at a swing or taunt on a new player and ends at the next switch, the NPC's death, 20 s with no
  swing or taunt (`AGGRO_QUIET_MS`, ending at the last event), or the pull end. That rule reproduces WCL's own
  threat table: on three pulls from three nights, 140 of 144 bands match to the millisecond, nothing ours calls
  aggro is missing from WCL's, and boss aggro never names a different player. The four differences are WCL
  holding a quiet band longer (see `docs/wcl/report-data.md`). Two pulls are unit tests against saved WCL tables.
  `aggro_holder(bands, npc_ids, t, instance)` answers who held an NPC at a time. Not used by the digest or
  blame yet.
- **Aggro for blame, reconciled with the Threat data (2026-10-11)**: compared over the 47
  Ula'tek pulls, per raid punishment:
  - The quiet-gap bands are the wrong question for punishments caused by nobody being in melee. The NPC
    stops swinging, so its band has often closed: 8 of 13 Mother's Wraths and 6 of 10 Rattler Slams fall
    outside any band. Blame wants the **last aggro holder**: the latest swing or taunt on that NPC within a
    lookback, with no quiet rule.
  - Mother's Wrath (raid): keep `cast_target`. The cast names the boss's actual target; in both cases where a
    taunt came first, it landed during the 5 s cast and the cast still named the original target.
  - Unchecked Rage: switch `melee_target` (damage hits only) to the last swing or taunt within 30 s. It
    charges `trMgApZCVYkv3jdq:18` at 393.5 s to Shinrael (Growl 2.8 s before) instead of Mekerakesh, and
    `trMgApZCVYkv3jdq:23` at 381.3 s, now charged to no one, to Mekerakesh (taunt 8.8 s before); 7 agree, and
    2 at the start of Stage Three have no holder either way.
  - Rattler Slam: same switch; identical results on these pulls (7 agree, 3 slams before anyone picked up
    the tail have no holder either way). Those unowned cases need the raid plan's tank assignments.
  - Desperate Thrash: judge each cone against the aggro holder of the casting add's instance
    (`source_instance`) instead of any recent melee target of the shared actor. Agrees on 93 of 102 first
    hits; it adds 4 hits on the other add's tank that today's rule misses and drops 1 false failure.
  - Positions would add "was the holder in melee range", which separates a tank out of position from other
    causes; attribution does not need it.
  Implemented: `blame: {by: aggro_holder, npc_game_id, ms}` (the latest swing or taunt on that NPC in the `ms`
  before, from `annotate.aggro_events`; none after the NPC's death) replaces `melee_target`, and
  `hits_on_non_tank` judges each player's first hit per cluster against the aggro holder of the casting NPC
  instance (a hit while the NPC has no holder is a failure). `ANNOTATION_VERSION` is 3. Over the 47 pulls the
  results match the comparison above: Unchecked Rage `trMgApZCVYkv3jdq:18` goes to Shinrael and
  `trMgApZCVYkv3jdq:23` at 381.3 s to Mekerakesh (2 rages now charged to no one, was 3); Mother's Wrath and
  Rattler Slam are unchanged; Desperate Thrash has 28 failures (was 24), the 4 new ones cone hits on the other
  add's tank. That leaves three kinds of failure attribution, each resting on one kind of evidence and testable with a few
  synthetic rows:
  1. **The player hit** (`any_hit`, `hits_per_cluster_gt`, `hit_amount_gt`, `aura_applied`,
     `debuff_duration_gt`): damage or aura rows on the failing player.
  2. **No one** (`not_interrupted`, `soak_count_lt`): an enemy cast or a whole cluster, with no single player
     at fault until raid-plan assignments exist.
  3. **The player responsible for an NPC** (`per_cluster` blame, `hits_on_non_tank`), from the Threat data
     only: `cast_target` (the NPC's own cast names its target) or `aggro_holder` (the NPC's latest swing or
     taunt), and for `hits_on_non_tank` the aggro holder of the casting NPC instance. `melee_target`, which
     read damage hits and missed taunts, is gone.
- **Positions: keep them, as a per-pull table (2026-10-16)**. Replaces "not kept yet" (2026-10-11). The
  measurements are in `docs/wcl/positions.md`, section "Position data". On the 47 Heroic Ula'tek pulls:
  - **Exact where it matters.** A damage row carries the hit player's position, so a raid-wide hit gives
    every player's position at that moment. Otherwise a player's latest sample is 0.1 s old at the median and
    0.5 s at p95, the same for tanks, melee and ranged. NPCs are sampled only on their own swings and casts,
    but they stand on fixed spots and their cast rows carry their position.
  - **Scale: 1 yard = 100 units**, from the run-speed peak (700 to 750 units/s against 7 yards/s). Measured
    radii fit it: Volatile Purge about 5 yards, the Serpent's Bite soak about 7, Spectral Coils about 30.
  - **Kept for:**
    - Aggro-holder blame (Unchecked Rage, Rattler Slam). The holder's distance and whether they were alive
      separate "out of position" from "died" and "nobody had aggro". Every one of the 15 punishments with a
      living holder had them beyond the NPC's swing range, and every holder in range had died.
    - Who stood in a soak. Serpent's Bite: 291 of 300 Ingested Venom recipients stood within 7 yards of a
      bitten player, against 2 of 45 non-recipients, which confirms the debuff marks soakers. Spectral Coils:
      2,292 of 2,293 Soul Constrictor recipients stood within 30 yards of their coil, so that debuff marks the
      players at the impact. The debuff rules (`aura_applied`, the planned stack and aura rules) remain the
      way to charge these; positions back them up and show who stood where.
    - Mephitic Thrash, once a human confirms the radius: positions are the only signal there.
  - **Not needed for:** Circling Prey and Volatile Purge. Their damage rules already find the same players,
    though Volatile Purge positions also show whose eruption a player stood in.
  - **Form:** a separate table per pull next to the events file. Columns: `t_ms`, `actor_id`, `instance`, `x`,
    `y`, one row per sample. That is +30% storage (+11.5 MB on 38.7 MB) against +39% for columns on every
    event row, and it keeps the events schema and `events_hash` as they are. `facing` is left out until a
    cone rule needs it. A hit's own position is the target's sample at the hit's `t_ms`.
  - **Query helper:** "position of actor A at t" as an as-of lookup that returns the sample's age, and "distance
    between A and B at t" built on it (1.5 to 3.3 ms for 1,000 probes per pull). NPCs are looked up by
    instance, with their latest swing or cast as the sample. Callers decide which age is too old. At a run
    speed of 7 yards/s, 0.5 s is 3.5 yards, enough to matter against a 5-yard radius.
- **Legacy comparison (M2.6, 2026-10-11)**: `docs/legacy-comparison.md`, run with the legacy tool live against the
  seven fixture pulls. Deaths agree exactly (136 of 136); every other difference is a legacy defect. It found four
  defects not in the plan's list (no cooldown is ever matched, pre-death windows hold only self-damage, killing
  blow and overkill always blank, ability-table IDs always 0), and one defect both sides share (the killing ability
  name, fixed on 2026-10-16).
- **Real-fixture goldens (2026-10-11)**: `tests/unit/encounter/test_real_fixtures.py` snapshots the first 20
  normalized events and the full digest of every recorded fixture pull (`tests/golden/{normalized,digests}/
  <code>_<fight>.json`), using the encounter files and registry under `config/`, and checks every rendered digest
  stays under 3,000 tokens. It runs in the unit suite (about 10 s) and skips when the Git LFS pages are not pulled.
  An edit to an encounter file or the registry changes these goldens; regenerate with `--update-goldens` and review
  the digest diff. Fixture ingest (`tests/helpers/encounter/real_fixtures.py`, shared with the perf tests) reads enemy Casts
  unfiltered, so editing an encounter file's ability IDs never leaves the fixtures without that data type.
- **Death rows take the killing blow (2026-10-16)**: WCL Deaths rows carry `abilityGameID: 0` and `sourceID: -1`,
  so every death record named "Unknown Ability (0)". Normalize now sets `ability_id` from `killingAbilityGameID`
  when `abilityGameID` is 0 or missing and `source_id` from `killerID` when `sourceID` is -1; there is no schema
  change. `death_records` treats a death ability of 0 as unknown, so events normalized earlier fall back to the
  last hit. This changes `events_hash` for every real pull, which invalidates cached findings; stored nights need
  an offline re-ingest. SYNTH01's death rows now use WCL's shape so the synthetic fixture exercises the same path.
- **Report metadata refresh (2026-10-16)**: `ingest --refresh-meta` (and `/ingest --refresh-meta`) refetches only
  `report.json`, then re-normalizes the pulls already in the store from the raw page cache and rewrites those
  whose metadata or events changed. It never requests event pages: fights not stored yet are left alone (named
  fights not stored are reported as failed), and a pull whose pages are missing from the cache fails. It needs
  WCL, so it is a usage error with `--offline`.
- **WCL phases and phase reached (2026-10-16)**: `GET_REPORT_META` requests `report.phases` and each `Pull`
  carries its encounter's `wcl_phases` (id, name, is_intermission; empty by default, so old `pulls.json` files
  load). The query's `encounterID` field was confirmed on 2026-10-17, when `--refresh-meta` refetched the three
  local reports. `phase_reached` stays the last detected
  encounter-file phase ID; without an encounter file it is WCL's name for the last `phase_transitions` entry, or
  its ID as text when unnamed. `phase_trigger_unmatched:<id>` (`annotate.unmatched_phases`) is raised for an
  undetected encounter-file phase whose `wcl_phase` trigger ID is among the pull's transitions; for undetected
  phases with other triggers, it is raised in file order for as many phases as the pull has distinct WCL
  transitions beyond the phases detected. `last_phase` is no longer read by any digest logic, and a kill no
  longer raises the anomaly by itself: a kill whose transitions match the detected phases is consistent.
  `/pulls` shows "Phase reached" (the WCL name) in place of "Last phase". Re-ingested offline, none of the 47
  recorded Heroic Ula'tek pulls (the ones with an encounter file) raises the anomaly.
- **Positions stored and used for blame (2026-10-17)**: implements "Positions: keep them, as a per-pull table".
  Contract text is in `docs/plan-changes.md` under `encounter/positions`.
  - **Table and hash.** Normalize collects samples in the same pass as the events
    (`normalize_pull_with_positions`; `normalize_pull` is unchanged), so `events_hash` and the normalized goldens
    stay as they were. The store writes `events/<report>/positions/<fight>.parquet` and records `positions_hash`
    in `pulls.json`; the hash function lives in `store.py` next to `POSITION_SCHEMA`, and the store hashes what
    it writes. A missing `positions_hash` makes ingest rewrite the pull, which is how stored nights get their
    tables. Measured on a copy of the three local nights, offline: all 62 stored pulls rewritten with
    `events_hash` unchanged, a second run skips all 62; the 47 Heroic Ula'tek tables hold 2,022,724 samples,
    11.4 MB against 38.7 MB of events (+30%), all on map 2610; normalize goes from 3.3 to 4.0 s, 5.0 to 6.6 s
    and 4.2 to 5.6 s per night (+0.8 to +1.6 s).
  - **Lookup helper.** `analysis/positions.py`: `position_at` (latest sample at or before `t`, with its age;
    callers pass `max_age_ms`, None for no limit) and `distance_yd` (None across maps). Blame needs a handful of
    lookups per pull, so it filters the table directly rather than using `join_asof`.
  - **State rules.** `no_holder` first, then `dead` (a Deaths row at or before the first hit and no cast,
    begincast or resurrect by or on the player since; a later death counts again), then positions. Distance
    and age are recorded only for living players with a position: a dead holder's last sample says where they
    died, not where they were at the hit. A missing NPC sample, or samples on two maps, is `no_position`.
  - **Age limit 1,500 ms for the player, none for the NPC.** The investigation saw living holders' samples up
    to 475 ms old; at the first hit (rather than the cast) the oldest was 484 ms for Rattler Slam, 100 ms for
    Unchecked Rage and 520 ms for Mother's Wrath targets. Bosses stand on fixed spots and are only sampled on
    their own swings and casts, so their latest sample is used however old.
  - **Reach values.** `reach_yd` is the p95 of each NPC's own swing distances rounded up, measured over the 47
    Heroic pulls from the swing row's NPC sample to the target's latest sample at most 1 s old: Ula'tek n=932,
    p95 20.4 yd, max 30.3 yd, so 21; Gore Rattle n=399, p95 17.1 yd, max 30.7 yd, so 18. 330 Ula'tek and 122
    Gore Rattle swing rows carry no NPC sample and were left out. These match the investigation.
  - **Every Unchecked Rage cast is a failure.** Ula'tek recasts it every 2.4 s while she stays untanked, so its
    `cluster_window_ms` went from 3,000 to 1,000, which keeps each cast its own cluster. One cast's hits land
    within 60 ms of each other, and the next cast's hits come 2,416 ms or more later. Each Rage hurts the raid
    again, so each counts. Over the 47 pulls that gives 21 failures (was 11): 9 `beyond_reach` (30.9 to 54.9
    yd, judged at each cast's first hit; 55.5 yd when judged at the cast itself), 10 `dead`, 2 `no_holder`,
    the investigation's split. Rattler Slam: 10 failures, 6 `beyond_reach` (21.1 to 51.9 yd), 1 `dead`, 3
    `no_holder`, as in the investigation. No living holder was in reach.
  - **Mother's Wrath has no `reach_yd`.** Its 13 failures: 2 targets dead, 11 alive at 18.0 to 75.1 yd from
    Ula'tek's latest sample. One living target was within her 21-yard reach (18.0 yd) and three more within her
    longest swing of 30.3 yd (22.6, 25.1 and 29.6 yd), so the split is not clean; its failures show `alive` with
    the distance.
  - **Cache keys.** The annotation cache sidecar stores `positions_hash` and misses when it differs;
    `ANNOTATION_VERSION` is 4. `PullDigest.produced_from` carries `positions_hash`; `findings.cache_key` takes it
    (via `review_graph.pull_cache_key`), and `EncounterData` keys its per-pull records on it, so cached
    annotations, records and reviews rebuild when positions change.
  - **Dumps.** The new `MechanicStat` and `MechanicCluster` fields are left out of dumps when empty or null
    (pydantic `exclude_if`), so mechanics without blame context serialize as before, and stored digests and
    findings load with the defaults.
  - **Pulls that span several maps (2026-10-18).** `mapID` is the UI map a unit's client was on, and an
    encounter can move part or all of the raid to another floor or area mid-pull, where coordinates share no
    frame with the first map. Nothing measures across maps (`distance_yd` returns None, as before). Blame now
    takes each NPC unit's latest sample (`latest_per_unit`), keeps the units whose latest sample is on the
    blamed player's map, and measures from the latest of those. Before, it took the latest sample of any NPC
    unit, so one add on another map hid one on the player's; filtering samples by the player's map instead
    would measure from where an NPC last stood before it left. Position dedup also keys on `map_id`.
    `ANNOTATION_VERSION` is 6. None of the 62 local pulls or the recorded fixtures has samples on two maps
    (every pull is on 2610, or 2632 for `H7z1fqDXjRTFnbtg:23`), so results on them are unchanged and the
    multi-map case is covered by unit tests only.
- **Stack and aura failure rules (2026-10-17)**: two new failure rules.
  - `debuff_stacks_gt {value}` (value >= 0): the mechanic's friendly `applydebuffstack` rows, and `applydebuff`
    rows that carry a stack, with `stack` above `value`, grouped into occurrences by the mechanic's
    `cluster_window_ms` gap. Each occurrence is one failure on its first row, charged to no one (like
    `soak_count_lt`): the stacks come from enemies left alive, not from a player standing somewhere. The rows are
    not hits, so they belong to no cluster. Ula'tek's Acidic Expulsion (1313531) uses `value: 2`; across the 47
    recorded pulls its stack rows reach 2 (65 rows), 3 (3) and 4 (1), all of the last two on
    `trMgApZCVYkv3jdq:26`, which gives 1 failure.
  - `hit_while_aura {aura_ids}` (non-empty): each player hit by the mechanic's damage while carrying any listed
    aura, judged once per cluster at their first hit and charged to the player hit. An aura is active from its
    apply (or a refresh or stack row with nothing open) to the matching remove, both ends inclusive, or to the
    pull end if never removed; a remove with no apply before it counts from the pull start. Aura IDs may also be
    in the mechanic's `ability_ids`; only damage rows are judged. No encounter uses it yet.
  - Validation: `aura_ids` is required by `hit_while_aura` and rejected on every other rule; `blame` stays
    rejected outside `per_cluster`. `ANNOTATION_VERSION` is now 5, and the optional `aura_ids` field changes
    every encounter file's hash once (the digest goldens differ only in `encfile_hash`).
  - **Soul Constrictor has no detectable signal**, so Ula'tek gets no rule for it. Over the 47 recorded pulls
    (209 Spectral Coils occurrences, Coils damage 1287265 clustered with its 3 s window), Soul Constrictor is
    aura 1300685: 2,400 `applydebuff` and 2,400 `removedebuff` rows, no refreshes or stacks, each lasting about
    6 s (median 6,001 ms) and landing 0 to 5 ms before the player's Coils hit. Coils within a burst are 3.1 to
    4.5 s apart, so a soaker is still debuffed at the next one.
    - Debuff count: the share of the raid receiving it per occurrence is not a steady half. The median is 45%,
      but it ranges from 0 to 95%: the first Coils of most pulls debuffs 20 of 21 or 21 of 22 players, and some
      pulls alternate two groups of about 10 (`H7z1fqDXjRTFnbtg:18`: 11, 10, 10, 11). It is never applied to a
      player who already has it (0 of 2,400), so a debuffed player soaking again leaves no aura row.
    - Hit signal: one damage ID for everyone, and every hit in an occurrence is the same size (the ratio to the
      occurrence median is 0.97 to 1.03 at the 5th and 95th percentiles, for players newly debuffed, already
      debuffed or neither). No other aura marks soakers.
    - `hit_while_aura` on 1300685 would charge all 699 hits on players still debuffed from the previous Coils,
      soakers or not, so it isn't added. The item stays in `docs/follow-ups.md`.
- **M2 exit criteria waiting on humans**: review of the H3 encounter file. Digest goldens, the token check, the perf
  budgets and the legacy comparison are done.
