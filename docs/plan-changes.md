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

## encounter/spike-logs

**Proposed, not implemented.** These come from the log investigations recorded in `docs/encounter/recorded-nights.md` ("Log
investigations", 2026-10-16). Apply them to the plan only once the code exists. Until then, the plan should only
mention them as open work.

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

## encounter/failure-rules

Built. Decisions D99 to D102 in `docs/decisions.md`. `ANNOTATION_VERSION` becomes 8.

### Refactor plan 6.2: encounter-file example, phases

In the encounter-file example, after the `p2` phase, add:

```yaml
  - id: intermission                 # repeating phases end the list; detection walks them again after the last
    name: Intermission
    difficulties: [heroic, mythic]
    repeat: true
    start: {trigger: wcl_phase, phase: 2}
  - id: stage
    name: Stage One again
    difficulties: [heroic, mythic]
    repeat: true
    start: {trigger: wcl_phase, phase: 1}
```

New paragraph after the example, before "Difficulties":

"**Repeating phases.** Phases with `repeat: true` form the repeating block: one run at the end of a difficulty's
phase list, never the first phase, whose triggers are `wcl_phase`, `boss_cast` or `boss_aura_applied` (a `time`
trigger fires once and `boss_hp_below` would refire at once). After the block's last phase fires, detection walks
the block again from its first phase, each trigger searched after the previous start, so each repeat gives one
span under the phase's one ID. Listing each repeat as its own phase still works. An unset `repeat` is left out of
the canonical dump, so files without it keep their `encfile_hash`."

### Refactor plan 6.2: failure-rules table

Replace the `per_cluster` and `hits_on_non_tank` rows and add two rows after `hit_while_aura`, so the table reads
(other rows unchanged):

| Rule | Params | A failure is | Charged to |
|---|---|---|---|
| `per_cluster` | `blame` (required) | One failure per cluster, on its first hit, for mechanics that punish the raid for one player's mistake, or (`by: nobody`) for occurrences that are the whole raid's failure | The player the blame spec points at; no one if it finds nobody or is `by: nobody` |
| `hits_on_non_tank` | `ms` | Each player hit (judged once per cluster, at their first hit) who was not the aggro holder of the NPC instance that hit them: the latest swing or taunt on that instance in the `ms` before. A hit while the NPC has no holder is a failure. | The player hit |
| `hits_on_non_target` | `ms` (> 0) | Each player hit (judged once per cluster, at their first hit) other than the friendly target of the latest enemy `cast` of one of the mechanic's own `ability_ids` in the `ms` up to the cluster's first hit (`begincast` rows carry no target and don't count). The completed cast names its original target, so a taunt during the cast doesn't move it. A cluster with no such cast is not judged | The player hit |
| `hit_without_aura` | `aura_ids` (non-empty) | Each player hit by the mechanic's damage (judged once per cluster, at their first hit) while carrying none of the listed auras. Aura activity as for `hit_while_aura` (both ends inclusive), so a hit that removes the aura in the same millisecond is protected. Aura IDs may also be in `ability_ids`; only damage rows are judged | The player hit |

Replace the blame-spec list's intro and add a third spec:

"**Blame specs** (only on `per_cluster`; `ms` > 0, default 10,000, on the two lookups):

- `{by: nobody}`: no one. Each occurrence is one failure of the whole raid, with `blamed_id` null and no blame
  context. It takes no other key. Use it when nobody owns the punishment (a droplet nobody stepped on, a fragment
  that reached its target)."

### Refactor plan 6.2: validation

Replace the validation paragraph's rule list with: "trigger, rule and blame names from the tables above; required
params present (`debuff_stacks_gt` needs `value` >= 0, `hit_while_aura` and `hit_without_aura` a non-empty
`aura_ids`, `hits_on_non_target` `ms` > 0); `blame` required by `per_cluster` and rejected elsewhere; `by: nobody`
rejects `ability_id`, `npc_game_id`, `ms` and `reach_yd`; `aura_ids` required by `hit_while_aura` and
`hit_without_aura` and rejected elsewhere; `ability_ids` required by `aura_removed_by` and rejected elsewhere, and
`followed_by_ids` and `on_death` allowed on `aura_removed_by` only; repeating phases form one run at the end of
each difficulty's phase list, never the first phase, with `wcl_phase`, `boss_cast` or `boss_aura_applied`
triggers; every difficulty used by an entry has at least one phase." The rest of the paragraph is unchanged.

### Refactor plan 6.3: blame context, anomaly and version

- In "Blame context", replace "for `per_cluster` failures (blame `aggro_holder` or `cast_target`)" with "for
  `per_cluster` failures whose blame is `aggro_holder` or `cast_target` (`by: nobody` gets none)".
- Replace "`ANNOTATION_VERSION` (7)" with "`ANNOTATION_VERSION` (8)".

### Refactor plan 6.4: `phase_trigger_unmatched`

In the anomaly codes list, replace the parenthesis after `phase_trigger_unmatched:<phase_id>` with: "(raised for an undetected encounter-file phase that WCL's phase transitions show the pull reached; never compared against `last_phase`). `wcl_phase` phases are judged by how often their WCL phase ID
occurs among the pull's transitions after 0 ms (the opening transition belongs to the `pull` phase): the n-th
listed phase with that ID is reached when the ID occurs at least n times, and a repeating one is flagged once when
the ID occurs more often than spans were detected for it. Listed phases with other triggers are flagged by count,
as before; repeating phases with other triggers are not judged."

## encounter/mechanic-descriptions

### Refactor plan 6.2: encounter file, mechanic `description`

In the encounter-file example, give each mechanic with a `failure` rule a `description` after its `name`, for
example under `swirl`:

```yaml
    description: Swirling flames on the ground. Every hit is a failure, charged to the player hit.
```

Add after the "Validation" paragraph: "`description` is one line of plain text, at most 300 characters: what the
mechanic does and, when it has a failure rule, what a failure means (who failed and how). It is required on every
mechanic with a `failure` rule and optional on the rest; damage windows have none. A description with a line break
or surrounding spaces is rejected. When set it is part of the canonical dump and so of `encfile_hash`; an unset
one is left out of the dump, so files without descriptions keep their hash. The digest does not show it."

### Refactor plan 6.5: the `mech` ref record

Replace the `mech:<id>` row's text with: "The `MechanicStat` (with `blame_states` and `blame_dist_yd` for
`per_cluster` mechanics), the encounter file's `description` of the mechanic when it has one, plus
`cluster_rows: list[MechanicCluster]`, whose rows on `per_cluster` mechanics carry `blamed`, `blame_state`,
`blame_dist_yd` and `blame_reach_yd`, so every number the MECHANICS line prints resolves". In "Resolved record
models", `mech` → `MechanicRecord` (`MechanicStat` plus `description?` (absent when the file gives none) and
`cluster_rows: list[MechanicCluster]`).

### Web portal plan: encounter names

The refactor plan does not cover the names route. Wherever the web portal plan lists
`GET /v1/encounters/{id}/names`, add: "`mechanic_descriptions: dict[str, str]`, mechanic ID to its description,
for the mechanics that have one (every mechanic with a failure rule), beside `mechanics`."

## web/mechanic-tooltips

### Refactor plan 6.2: encounter file, mechanic `icon_ability_id`

Add to the mechanic fields after `ability_ids`: "`icon_ability_id` (optional): which of the mechanic's
`ability_ids` stands for it as an icon; one outside them is a load error. Unset, the first ability ID is used. It is
part of the canonical dump only when set, so files without it keep their hash."

### Web portal plan: encounter names

Wherever the web portal plan lists `GET /v1/encounters/{id}/names`, add: "`mechanic_info: dict[str, MechanicInfo]`
for every mechanic: `ability_ids`, `icon_ability_id` (resolved), `kind`, `severity` and `failure_summary` (the
failure rule in plain words, null without a rule)." In the pull page section, add: "Mechanics get KhadBot's own
tooltip (description and failure summary) on chips, table names and timeline lane labels, and a card tooltip on
timeline mechanic markers; spells keep Wowhead's. The mechanic panel shows the description, the failure summary,
the tiles, failures by player, the clusters with their blame context, the deaths in its window and its abilities as
Wowhead links."
