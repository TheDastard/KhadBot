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
