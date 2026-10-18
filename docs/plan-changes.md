# KhadBot plan changes

Contract changes waiting to be applied to the design plans: the "KhadBot Encounter Review Refactor" plan
(https://claude.ai/artifact/Q45H17JuH5EXK8oz17CzQE) and the web portal plan
(https://claude.ai/artifact/RWKPP7XyMXb6Gb2iFGhMqD). Branches don't edit the plan artifacts themselves; they
record their changes here, and a closing plan-sync pass applies every entry to the artifacts and empties this
file.

A contract change is anything the plans specify: models, file formats, evidence refs, failure rules, digest
fields, the API surface.

How to add an entry:

- Add a `##` heading named after your branch (for example `## encounter/ingest-metadata`) and put all of that
  branch's changes under it.
- For each change, name the plan and section it changes, then write the replacement text for that section, not
  just a description of the change.
- Only add to this file. Never rewrite or reorder another branch's heading. On a merge conflict, keep both sides.

## repo/ci

Work plan (Polish and Digest Dashboard), decision R15, rule P2, the E0 card's `mise.toml`, and every task card
that names a mise task: tasks that are variants of one group use a `group:` prefix. Replace the names as follows.

| Plan text | Task as built |
|---|---|
| `mise run test-unit` / `test-integ` / `test-eval` | `mise run test:unit` / `test:integ` / `test:eval` |
| `mise run gen-contracts` | `mise run gen:contracts` |
| `mise run fixture-data` | `mise run gen:fixture-data` |
| `mise run distclean` | `mise run clean:all` |
| (none) | `mise run install:runtime`, `mise run install:cli`; bare `install` stays the full dev install |

New tasks follow the same rule: D2's web tasks become `gen:types` (not `gen-types`), and `dev-web` and `e2e` take a
group prefix where they have siblings (for example `web:dev`, `test:e2e`).

## encounter/ingest-metadata

### Refactor plan 6.1 (Ingest models)

Add above `Pull`, and add the field to `Pull` after `friendly_player_ids`:

```
class WclPhase(BaseModel):         # one of an encounter's phases as WCL names them (report.phases)
    id: int
    name: str
    is_intermission: bool = False

class Pull(BaseModel):
    ...
    wcl_phases: list[WclPhase] = []    # the encounter's phases from report.phases; names the transition IDs
```

Replace the "Phase reached" bullet under **Pull facts from real reports** with:

- **Phase reached** is the last `phase_transitions` ID, named through `report.phases` (`id name isIntermission`),
  which ingest stores on each pull as `wcl_phases` (`GET_REPORT_META` requests
  `phases { encounterID phases { id name isIntermission } }`, confirmed on a live
  response). `last_phase` numbers stages and intermissions separately, so Ula'tek's intermission
  (phase ID 3) reports `last_phase=1`; never compare it with phase IDs, and no digest logic reads it.

Add a bullet:

- **Death rows** from WCL carry `abilityGameID: 0` and `sourceID: -1`, with the killing blow in
  `killingAbilityGameID` and the killer in `killerID`. Normalize sets `ability_id` from `killingAbilityGameID` when
  `abilityGameID` is 0 or missing, and `source_id` from `killerID` when `sourceID` is -1, for Deaths `death` rows.
  The event schema does not change. Death records treat a death ability of 0 as unknown, so events normalized
  before this rule fall back to the last hit.

Add after the `events_hash` paragraph:

`ingest --refresh-meta` refetches only `report.json` and re-normalizes the pulls already stored from the raw page
cache, rewriting those whose metadata or events changed. It never requests event pages and cannot run with
`--offline`.

### Refactor plan 6.4 (Digest)

Replace the `phase_reached` comment in `PullDigest` with:

```
    phase_reached: str | None           # last detected encounter-file phase ID; without an encounter file, WCL's
                                        # name for the last phase transition (its ID as text when unnamed)
```

Replace the `phase_trigger_unmatched` anomaly text with:

`phase_trigger_unmatched:<phase_id>` (raised for an undetected encounter-file phase that WCL's phase transitions
show the pull reached: a phase with a `wcl_phase` trigger when its WCL phase ID is among the pull's transitions;
undetected phases with other triggers, in file order, for as many phases as the pull has distinct WCL transitions
beyond the phases detected. Never compared against `last_phase`).

## encounter/spike-logs

**Proposed, not implemented.** These come from the log investigations recorded in `docs/decisions.md` ("Log
investigations", 2026-10-16). Apply them to the plan only once the code exists. Until then, the plan should only
mention them as open work.

### Refactor plan 6.2: `aura_removed_by` failure rule

New row for the failure-rules table, in the "charged to the player" group:

| Rule | Params | A failure is | Charged to |
|---|---|---|---|
| `aura_removed_by` | `ability_ids` (non-empty), `on_death` (default false) | Each removal of one of the mechanic's auras from a player that is followed within 200 ms by a hit on that player from one of `ability_ids`. With `on_death: true`, also each removal followed within 200 ms by that player's death. The remove row is the failure row; it belongs to no cluster. | The player who lost the aura; no one for a removal at a death |

Add to the validation paragraph: "`aura_removed_by` requires a non-empty `ability_ids`; those IDs may belong to
other mechanics, since they are a parameter, not the mechanic's own abilities."

Add after the blame-spec list: "A hatch or spawn has no event of its own in the logs. When an add comes from an
aura breaking on a player (an egg carried as a debuff), charge the break with `aura_removed_by` on the aura's
mechanic, not a blame spec on the add's damage. The add's damage forms many clusters over its life, not one per
spawn."

Add to 6.3 **Clusters**: "...the aura rows of `aura_applied` and the remove rows of `aura_removed_by`) belong to
no cluster".

Ula'tek example, for the file once the rule exists:

```yaml
  - id: malignant_shell
    name: Malignant Shell
    # A shell broken by Caustic Waves hatches a Blightscale Viper (29 of 29 on the recorded pulls); one broken
    # by Spectral Coils hatches a Rawling (746 of 747). The carrier's death counts but is charged to no one.
    # kind and severity are unchanged here; the file review decides them.
    difficulties: [heroic]
    ability_ids: [1297213, 1295360]
    kind: unavoidable
    failure: {rule: aura_removed_by, ability_ids: [1292403], on_death: true}
    severity: low
```

### Refactor plan 6.1: DamageDone fetch

Add `"DamageDone"` to `FetchSpec.data_type`. Add to the default fetch specs: "**DamageDone** (Friendlies):
damage by players and their pets to NPCs, the only source of damage amounts on enemies. Used by kill targets
(6.2). Normalize keeps rows whose source is friendly and whose target is an NPC." The fields and cost have to
be confirmed on the live schema first (see `docs/follow-ups.md`, "Wrong-target damage").

### Refactor plan 6.2: kill targets in the encounter file

Add to the encounter-file example, after `damage_windows`:

```yaml
kill_targets:                        # who the raid should be damaging, and when
  - id: heart_during_rage
    name: Venomous Heart during Rage of the Shackled
    difficulties: [heroic]
    during: {damage_window: rage_of_the_shackled}
    # other spans: {phase: <phase_id>}, {npc_alive: <npc_game_id>}
    npc_game_ids: [267460]
    ignore_npc_game_ids: []          # NPCs whose damage counts as neither on nor off target
    failure: {rule: off_target_share_gt, value: 50}
```

New text: "A **kill target** names the NPCs (by game ID) the raid should be damaging during a span. Spans:
`{damage_window: id}` is each occurrence of that window, from its first hit to its last;
`{phase: id}` is that phase; `{npc_alive: game_id}` is each instance of that NPC, from its first event to its
death in the Threat data or the pull end (for a priority add). Only DamageDone rows count. Pets count as their
owner. Damage on an NPC that is in neither list is off target."

New row for the failure-rules table, "charged to the player" group:

| Rule | Params | A failure is | Charged to |
|---|---|---|---|
| `off_target_share_gt` | `value` (0 to 100) | Only on kill targets. Each player whose off-target damage within one span is more than `value` percent of their damage to NPCs in that span. One failure per player per span, on their first off-target row. It belongs to no cluster. | The player |

Validation additions: kill-target IDs are unique per difficulty; `during` names exactly one of `damage_window`,
`phase` or `npc_alive`, and a named window or phase exists in the same difficulty view; `npc_game_ids` is
non-empty and doesn't overlap `ignore_npc_game_ids`; kill targets accept only `off_target_share_gt`, and that
rule is rejected on mechanics.

### Refactor plan 6.3, 6.4, 6.5: kill-target records, digest and ref

6.3, new record:

```python
class KillTargetStat(BaseModel):
    ref: Ref                             # ev:<report>:<fight>:kill:<kill_target_id>
    kill_target_id: str
    spans: int
    on_target_pct: float                 # raid damage on the kill target / raid damage to NPCs in the spans, 1 decimal
    off_target_by_npc: dict[str, int]    # NPC name -> raid damage, top 3
    failures: int
    failures_by_player: dict[str, int]   # player name -> failures, top 10
```

6.4: add `kill_targets: list[KillTargetStat] = []` to `PullDigest` (one per kill target in the view). The
`KILL TARGETS` section renders one line per target under its ref, for example
`heart_during_rage x2: 81.0% on target; off: Ula'tek 1.2M; Dastard 2, Moombly 1`. A pull with no DamageDone
data gets the anomaly `missing_data_type:DamageDone` and no section.

6.5: new ref kind `kill:<kill_target_id>`, which resolves to the `KillTargetStat` plus
`span_rows: list[{n, t_ms, end_ms, on_target_pct, failed_players}]`, so every number in the line verifies.

## encounter/positions

### Refactor plan 5.2 (Data directory)

Add under `events/<report>/<fight>.parquet`:

```
  events/<report>/positions/<fight>.parquet             # position samples per pull (6.1)
```

### Refactor plan 6.1 (Ingest models)

Add after the normalized event schema table, before the `events_hash` paragraph:

#### Position table (polars, one parquet per pull)

`events/<report>/positions/<fight>.parquet`, written atomically next to the events file. One row per sample:

| Column | Type | Source / rule |
|---|---|---|
| `t_ms` | Int32 | `timestamp - pull.start_ms` |
| `actor_id` | Int32 | the unit `resourceActor` names: 1 is `sourceID`, 2 is `targetID` |
| `instance` | Int32 (nullable) | `sourceInstance` or `targetInstance` to match; null for unique actors and players |
| `x`, `y` | Int32 | WCL map units, 1/100 yard (players run 700 to 750 units/s at 7 yards/s); stored raw |
| `map_id` | Int32 (nullable) | `mapID` |

Normalize takes a sample from every raw row that carries `x`, in the same pass that builds the events, from
every data type, including the Threat rows it drops from the events. A row with no `resourceActor` is skipped.
Duplicate samples on (`t_ms`, `actor_id`, `instance`, `x`, `y`) are dropped, and rows are sorted by `actor_id`,
`instance`, `t_ms` (then `x`, `y`, `map_id`). `facing` is not kept. The events frame and `events_hash` do not
change. On the 47 Heroic Ula'tek pulls the tables hold 2.02 M samples, 11.4 MB against 38.7 MB of events (+30%).

Replace the `events_hash` paragraph with:

`events_hash` = SHA-256 of the parquet's sorted rows serialized with `orjson` (column order fixed by the schema).
`positions_hash` is the same over the position table's rows in its sort order. Both are stored in `pulls.json`
next to each pull: `{"pull": Pull, "events_hash": str, "positions_hash": str | null, "truncated": [dir_name]}`.
A record written before positions were kept has no `positions_hash` and reads as an empty position table. Ingest
skips a pull only when `events_hash`, `positions_hash` and the pull metadata all match, so an offline re-ingest
adds the table to pulls stored without one.

`EventStore` gains `positions(pull_key) -> DataFrame` (an empty frame when the pull has no table) and
`positions_hash(pull_key) -> str | None`; `write_pull(pull, df, events_hash, truncated=None, positions=None)`
writes the table and its hash when `positions` is given, and removes a stale table when it is not.

### Refactor plan 6.2 (Configuration files)

In the blame spec list, replace the two bullets with:

- `{by: cast_target, ability_id, ms, reach_yd?}`: the friendly target of the latest enemy `cast` of
  `ability_id` in the `ms` before the occurrence. The boss's own cast names its target; use it when the boss has
  such a cast (Mother's Wrath).
- `{by: aggro_holder, npc_game_id, ms, reach_yd?}`: the aggro holder of the NPC with that game ID (matched by game
  ID because a boss can have two actors with one name): the player behind its latest melee swing or taunt in the
  Threat data in the `ms` before; nobody after the NPC's death. Use it when the punishment fires because nobody is
  in melee (an untanked boss or tail), since the NPC has stopped swinging by then.

Add after them:

`reach_yd` (optional, a positive number) is the NPC's swing reach in yards: the caster of the cast for
`cast_target`, the NPC with `npc_game_id` for `aggro_holder`. With it, annotation judges a living blamed player as
in or beyond reach (6.3); without it, only as alive. Validation rejects zero and negative values. Set it from the
NPC's own melee swings: the p95 of the distance from the NPC to the swing's target (target sample at most 1 s
old), rounded up to a whole yard. Heroic Ula'tek: `unchecked_rage` 21 (Ula'tek, n=932, p95 20.4, max 30.3) and
`rattler_slam` 18 (Gore Rattle, n=399, p95 17.1, max 30.7). `mothers_wrath_raid` has none.

In the encounter file example, change the `no_tank_in_melee` blame line to:

```
      blame: {by: aggro_holder, npc_game_id: 555555, ms: 30000, reach_yd: 21}
```

### Refactor plan 6.3 (Analysis records)

In `MechanicStat`, add after `deaths_in_window`:

```
    blame_states: dict[str, int] = {}    # per_cluster mechanics: failures per blame_state; left out of dumps when empty
    blame_dist_yd: dict[str, tuple[float, float]] = {}   # per_cluster mechanics: (min, max) distance in yards of
                                         # the blamed players per state that has distances; left out when empty
```

In `MechanicCluster`, add after `failed_players` (each left out of dumps when null):

```
    blamed: str | None = None            # per_cluster: the player charged with the cluster's failure
    blame_state: str | None = None       # per_cluster: where they were at the first hit (states below)
    blame_dist_yd: float | None = None   # their distance from the NPC in yards, 0.1
    blame_reach_yd: float | None = None  # the blame spec's reach_yd, when one is set
```

Add after the **Blame** paragraph:

**Blame context:** for `per_cluster` failures (blame `aggro_holder` or `cast_target`) annotation records where the
blamed player was when the occurrence's first hit landed, in three more columns set on those failure rows only
and null elsewhere: `blame_state` (Utf8), `blame_dist_yd` (Float32, yards rounded to 0.1) and `blame_pos_age_ms`
(Int32, the age of the player's sample). States, in order of precedence:

- `no_holder`: `blamed_id` is null.
- `dead`: the player has a Deaths row at or before the hit and no cast or resurrect since.
- `no_position`: no player sample at or before the hit within 1,500 ms, no NPC sample, or the two on different
  maps.
- `alive`: alive with a position, and the blame sets no `reach_yd`.
- `beyond_reach` / `in_reach`: alive and farther than / within `reach_yd` of the NPC.

The player's position is their latest sample at or before the first hit, at most 1,500 ms old (a raid-wide hit
carries the hit player's own position). The NPC's is its latest sample among the blame's NPC actors (the cast's
caster for `cast_target`), with no age limit: the bosses stand on fixed spots and are sampled on their own swings
and casts. Distance and age are null for `dead`, `no_position` and `no_holder`. `annotate()` takes the pull's
position table (`positions=`), `annotate_pull` reads it from the store, and the annotation cache also keys on
`positions_hash`. `ANNOTATION_VERSION` is 4.

### Refactor plan 6.4 (Digest)

Replace the `produced_from` comment in `PullDigest` with:

```
    produced_from: dict[str, str]       # events_hash, positions_hash, encfile_hash, registry_hash
```

Add a bullet:

- A mechanic with blame context ends its MECHANICS line with a clause per state, in the order beyond reach, in
  reach, alive, dead, no position, none, with the distance range in whole yards where the state has distances,
  for example `blamed: 2 beyond reach (31–55 yd), 1 dead, 1 none`. On the 47 Heroic Ula'tek pulls it adds at most
  22 tokens to a rendered digest (1,403 to 2,192 tokens).

### Refactor plan 6.5 (Evidence refs)

Replace the `mech:<id>` row with:

| `mech:<id>` | The `MechanicStat` (with `blame_states` and `blame_dist_yd` for `per_cluster` mechanics) plus `cluster_rows: list[MechanicCluster]`, whose rows on `per_cluster` mechanics carry `blamed`, `blame_state`, `blame_dist_yd` and `blame_reach_yd`, so every number the MECHANICS line prints resolves |

### Refactor plan 6.8 (Findings cache)

Replace the `cache_key` line with:

```
cache_key = sha256(pull_key, events_hash, positions_hash, encfile_hash, registry_hash,
                   prompt_version, graph_version, model_id, depth)[:16]
```

`positions_hash` comes from the digest's `produced_from` (empty for a pull stored without a position table).
`EncounterData` also keys its per-pull records on it.
