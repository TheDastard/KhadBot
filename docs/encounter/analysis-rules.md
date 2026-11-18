# Encounter analysis rules

How annotation and analysis read a pull: what the encounter file declares, what counts as a hit, a failure and an
anomaly, who a failure is charged to, and how aggro and positions feed that. The stages around them are in
`pipeline.md`; what the recorded nights showed is in `recorded-nights.md`.

## Encounter files

- **Path.** `config/encounters/<zone_id>-<zone_slug>/<encounter_id>-<encounter_slug>.yaml` (Ula'tek:
  `53-the_venomous_abyss/3492-ulatek.yaml`). The zone ID is WCL's, the only instance ID the data carries. Slugs are
  lowercase, apostrophes dropped, other runs of non-alphanumerics turned into `_`. The file declares
  `zone: {id, name}`; the loader rejects a path that doesn't match the declared zone, encounter ID and name, and an
  encounter ID with two files.
- **Difficulties.** Every entry (phase, mechanic, damage window, ignored ability) has a required `difficulties` list
  (`lfr`, `normal`, `heroic`, `mythic`; WCL codes 1, 3, 4, 5, of which 4 and 5 are confirmed on recorded reports).
  Validation runs per difficulty, so an ID may repeat on entries whose difficulties don't overlap. Analysis uses
  `load_encounter_view(encounter_id, pull.difficulty)`: that difficulty's entries only, or None (coverage `none`)
  when no phase covers it. `encfile_hash` is taken over the view, so editing one difficulty leaves the others
  cached. The ingest filter uses every difficulty's ability IDs.
- **Unknown keys are errors** (`extra="forbid"`), so typos fail at load.
- **Triggers that match nothing yet.** `boss_hp_below` needs damage on the boss (no DamageDone spec) and
  `boss_aura_applied` needs auras on the boss (Buffs are fetched for friendlies only). Use `boss_cast`, `wcl_phase`
  or `time`.

## Hits, clusters and windows

- A **hit** is a `damage` event on a friendly target. Clusters, `window_occurrence`, `MechanicStat.hits` and
  `damage_taken`, and the death window all use hits. Damage figures use WCL `amount` (after absorbs and overkill).
- **Window coverage counts `target: raid` cooldowns only.** A damage window is raid-wide damage; an external on one
  player does not cover it.
- **`time_to_die_ms`** is None when the actor had no reading at 90% HP or more in that life. A second death's
  window and time-to-die are clipped to after the previous death (battle res).
- **Death rows take the killing blow.** WCL Deaths rows carry `abilityGameID: 0` and `sourceID: -1`, so normalize
  sets `ability_id` from `killingAbilityGameID` and `source_id` from `killerID`. `death_records` treats an ability
  of 0 as unknown and falls back to the last hit.

## Phases and anomalies

- `GET_REPORT_META` requests `report.phases`, and each `Pull` carries its encounter's `wcl_phases` (id, name,
  is_intermission). `phase_reached` is the last detected encounter-file phase; without an encounter file it is WCL's
  name for the last transition, or its ID as text.
- **`phase_trigger_unmatched:<id>`** (`annotate.unmatched_phases`) is raised for an undetected phase whose
  `wcl_phase` trigger ID is among the pull's transitions; for phases with other triggers, in file order for as many
  phases as the pull has distinct WCL transitions beyond those detected. An early wipe that never reached a phase is
  not an anomaly, and a kill doesn't raise it by itself.
- **`missing_data_type:<T>`** checks only DamageTaken, Casts and Healing, which every real pull has; zero Deaths,
  Interrupts or Dispels rows are legitimate.
- **`unregistered_damage:<pct>`** is raised only when an encounter file exists and the share is at least 10%.

## Failure rules and blame

Annotation adds a `blamed_id` column, the player charged with each failure row. `MechanicStat.failures_by_player`
and `MechanicCluster.failed_players` count `blamed_id`, and an `event:` ref on a failure row adds `blamed` (a name).

Failures are charged by one of three kinds of evidence:

1. **The player hit**: `any_hit`, `hits_per_cluster_gt`, `hit_amount_gt`, `aura_applied`, `debuff_duration_gt`,
   `hit_while_aura`.
2. **No one**: `not_interrupted` (the enemy cast row), `soak_count_lt` (the cluster's first row),
   `debuff_stacks_gt`, until raid-plan assignments exist.
3. **The player responsible for an NPC**, from the Threat data only: `per_cluster` with `blame`, and
   `hits_on_non_tank`.

The rules that need explaining:

- **`per_cluster` + `blame`**: one failure per cluster, on its first hit. `blame` is required here and rejected on
  every other rule.
  - `{by: cast_target, ability_id, ms}` charges the friendly target of the latest enemy cast of `ability_id` in the
    `ms` before. The cast names the boss's real target even when a taunt lands during it.
  - `{by: aggro_holder, npc_game_id, ms}` charges the player behind the NPC's latest melee swing or taunt in the
    `ms` before (from `annotate.aggro_events`, none after the NPC's death). It matches by game ID, since a boss can
    have two actors with one name. This is the **last** aggro holder with no quiet rule, because punishments for
    nobody being in melee happen after the NPC stops swinging.
  - With no match the failure still counts, charged to no one.
- **`hit_amount_gt {value}`**: each hit above `value` before mitigation (`unmitigated`, else `amount + absorbed`).
- **`aura_applied`**: each application of the mechanic's aura on a player. Not a hit; belongs to no cluster.
- **`hits_on_non_tank {ms}`**: one failure per player per cluster for players other than the aggro holder of the
  NPC instance that hit them, judged at their first hit (NPCs stop swinging while they cast, so later DoT ticks on
  the tank would read as failures). Instances matter because adds of one kind can share an actor ID.
- **`debuff_stacks_gt {value}`**: the mechanic's friendly `applydebuffstack` rows (and `applydebuff` rows with a
  stack) above `value`, grouped by `cluster_window_ms`; one failure per occurrence, charged to no one, since the
  stacks come from enemies left alive.
- **`hit_while_aura {aura_ids}`**: each player hit while carrying any listed aura, judged once per cluster at the
  first hit. An aura is active from its apply (or a refresh or stack with nothing open) to the matching remove,
  inclusive, or to the pull end; a remove with nothing before it counts from the pull start. `aura_ids` is required
  here and rejected elsewhere.

## Aggro

- **Threat data.** Ingest fetches `Threat` (Friendlies side, about 1 point a pull). It has no threat values: it is
  NPC melee swings (misses included), taunts and NPC deaths. Normalize keeps those and drops players' taunt casts,
  which repeat Casts. The event schema carries `source_instance` and `target_instance`, which number NPCs sharing
  an actor ID; event rows and death top hits name NPCs with their instance (`Blightscale Rawling #51`).
- **Digest.** `aggro` lists per boss NPC its swings, top 3 targets with shares, and taunts by player, rendered as
  `AGGRO`; the `pull` ref resolves with the same list so verify can check it.
- **Aggro bands** (`analysis/aggro.py`): a band starts at a swing or taunt on a new player and ends at the next
  switch, the NPC's death, 20 s with no swing or taunt (`AGGRO_QUIET_MS`), or the pull end. This reproduces WCL's
  threat table: 140 of 144 bands match to the millisecond on three pulls, the rest are WCL holding a quiet band
  longer (`docs/wcl/report-data.md`). `aggro_holder(bands, npc_ids, t, instance)` answers who held an NPC; the
  digest and blame don't use the bands.

## Positions

The measurements behind this are in `docs/wcl/positions.md`.

- **Storage.** Normalize collects samples in the same pass as the events (`normalize_pull_with_positions`) and the
  store writes `events/<report>/positions/<fight>.parquet` (`t_ms`, `actor_id`, `instance`, `x`, `y`, `map_id`),
  recording `positions_hash` in `pulls.json`. A separate table costs +30% storage against +39% for columns on every
  event row and leaves `events_hash` alone. `facing` is not kept. A missing `positions_hash` makes ingest rewrite
  the pull.
- **Scale.** 1 yard = 100 units, from the run-speed peak (700 to 750 units/s against 7 yards/s).
- **Lookups** (`analysis/positions.py`): `position_at` (latest sample at or before `t`, with its age; callers pass
  `max_age_ms`) and `distance_yd` (None across maps). A hit row carries the hit player's exact position; otherwise a
  player's latest sample is 0.1 s old at the median and 0.5 s at p95. NPCs are sampled only on their own swings and
  casts.
- **Blame context** for aggro-holder blame, in order: `no_holder`; `dead` (a Deaths row at or before the first hit
  with no cast, begincast or resurrect since); then distance and age, recorded only for living players with a
  position. A missing NPC sample, or samples on two maps, is `no_position`. The player's sample may be at most
  1,500 ms old; the NPC's latest is used however old, since bosses stand on fixed spots.
- **Reach.** `reach_yd` is the p95 of the NPC's own swing distances rounded up: Ula'tek 21 yd (p95 20.4, max 30.3,
  n=932), Gore Rattle 18 yd (p95 17.1, max 30.7, n=399).
- **Several maps.** `mapID` is the UI map a unit's client was on. Blame takes each NPC unit's latest sample, keeps
  units whose latest sample is on the blamed player's map, and measures from the latest of those. No recorded pull
  has samples on two maps; unit tests cover it.
- **Cache keys.** The annotation sidecar stores `positions_hash`; `PullDigest.produced_from`, `findings.cache_key`
  and the `EncounterData` records key all take it.
- **Dumps.** The blame-context fields of `MechanicStat` and `MechanicCluster` are left out when empty, so mechanics
  without blame serialize as before.

## Fixtures and goldens

- `tests/unit/encounter/test_real_fixtures.py` snapshots the first 20 normalized events and the full digest of
  every recorded fixture pull (`tests/golden/{normalized,digests}/`) using `config/`, and checks each digest stays
  under the token ceiling. It skips without the Git LFS pages. An edit to an encounter file or registry changes
  these goldens: regenerate with `pytest --update-goldens` and review the diff.
- Fixture ingest (`tests/helpers/encounter/real_fixtures.py`) reads enemy Casts unfiltered, so an encounter file's
  ability IDs never leave the fixtures without that data type.
- Offline ingest of SYNTH01 through the CLI misses the cache: the CLI derives filters from `config/` while SYNTH01
  was recorded with synthetic IDs. Tests pass them explicitly.
