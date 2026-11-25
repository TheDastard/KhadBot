# Failure rules

Every condition an encounter file can mark as a failure, and who each one is charged to. A mechanic takes at most
one rule, as `failure: {rule: <name>, ...}`; the names and parameters are validated by `FailureRule` and `Blame` in
`src/khadbot/encounter/config_files.py`, and `annotate/failures.py` applies them.

## How failures are recorded

- **Failure rows.** Annotation sets `is_failure` on the row that is the failure (a hit, an aura row, a cast) and
  `blamed_id` to the player charged, or null when no one is. `MechanicStat.failures_by_player` and
  `MechanicCluster.failed_players` count `blamed_id`, and an `event:` ref on a failure row adds `blamed` (a name).
- **Hits** are the mechanic's `damage` rows on friendly targets. Hits closer than the mechanic's
  `cluster_window_ms` (default 1,000) form one cluster, one occurrence of the mechanic.
- **Once per cluster.** Rules marked "per player per cluster" judge each player at their first hit in a cluster,
  so later ticks of the same occurrence never add failures.
- **Aura spans**, used by the aura rules: an aura is active from its apply (or a refresh or stack with nothing
  open) to the matching remove, both ends inclusive, or to the pull end; a remove with nothing before it counts
  from the pull start.

## Damage taken

The player hit is charged.

| Rule | Params | A failure is |
|---|---|---|
| `any_hit` | none | Every hit |
| `hits_per_cluster_gt` | `value` | The hit that takes a player past `value` hits in one cluster: one failure per player per cluster |
| `hit_amount_gt` | `value` | Each hit above `value` before mitigation (`unmitigated`, else `amount + absorbed`) |

## Damage judged against auras

The player hit is charged; per player per cluster. `aura_ids` is required by both and rejected on every other rule.

| Rule | Params | A failure is |
|---|---|---|
| `hit_while_aura` | `aura_ids` | A hit on a player carrying any listed aura (a debuffed player standing in the raid, a carrier who shouldn't be hit) |
| `hit_without_aura` | `aura_ids` | A hit on a player carrying none of the listed auras (a bystander near a carrier, a player outside a protective zone) |

`hit_without_aura` has no grace window: damage that lands a millisecond after the aura's remove counts as
unprotected, so it charges carriers on mechanics whose damage follows the aura's end.

## Damage on the wrong player

The player hit is charged; per player per cluster.

| Rule | Params | A failure is |
|---|---|---|
| `hits_on_non_tank` | `ms` | A hit from an NPC on anyone other than its aggro holder: the player behind the latest melee swing or taunt on that NPC instance in the `ms` before, from the Threat data. A hit while the NPC has no holder is a failure too |
| `hits_on_non_target` | `ms` (> 0) | A hit on anyone other than the friendly target of the latest completed enemy `cast` of one of the mechanic's own `ability_ids` in the `ms` up to the cluster's first hit |

- `hits_on_non_tank` judges the first hit because NPCs stop swinging while they cast, so later DoT ticks on the
  tank would read as failures. Instances matter because adds of one kind can share an actor ID.
- `hits_on_non_target` reads completed `cast` rows only: `begincast` rows carry no target. The completed cast keeps
  its target through a taunt during the cast, which `hits_on_non_tank` would follow. A cluster with no such cast is
  not judged. The cast's ID must be in the mechanic's `ability_ids`, which also puts it in the enemy Casts filter.

## Occurrences

One failure per cluster, on its first hit.

| Rule | Params | A failure is | Charged to |
|---|---|---|---|
| `soak_count_lt` | `value` | A cluster hitting fewer than `value` distinct players | No one, until raid-plan assignments exist |
| `per_cluster` | `blame` (required) | Every cluster: for mechanics that punish the raid for one player's mistake, or that are the whole raid's failure | The player the blame spec points at; no one if it finds nobody or is `by: nobody` |

`blame` is required on `per_cluster` and rejected on every other rule. It is looked up at the cluster's first hit:

| Blame spec | Charges |
|---|---|
| `{by: cast_target, ability_id, ms}` | The friendly target of the latest enemy cast of `ability_id` in the `ms` before. The cast names the boss's real target even when a taunt lands during it |
| `{by: aggro_holder, npc_game_id, ms}` | The player behind the NPC's latest melee swing or taunt in the `ms` before (none after the NPC's death). It matches by game ID, since a boss can have two actors with one name. This is the **last** holder with no quiet rule, because punishments for nobody being in melee happen after the NPC stops swinging |
| `{by: nobody}` | No one, and no blame context is recorded. It takes no other key |

`ms` defaults to 10,000. `reach_yd` (optional, either lookup) is the NPC's swing reach: with it, the blame context
judges a living blamed player as in or beyond reach at the hit (from positions); without it, only as alive.

## Auras and debuffs

These judge aura rows, not hits, so their failures belong to no cluster.

| Rule | Params | A failure is | Charged to |
|---|---|---|---|
| `aura_applied` | none | Each application of the mechanic's aura (`applydebuff` or `applybuff`) on a player | The player |
| `debuff_duration_gt` | `ms` | A debuff application kept longer than `ms` (or still open at the pull end past `ms`); the apply row is the failure | The player |
| `debuff_stacks_gt` | `value` (>= 0) | The mechanic's friendly stack rows above `value`, grouped by `cluster_window_ms`: one failure per occurrence, on its first row | No one, since the stacks come from enemies left alive |
| `aura_removed_by` | `ability_ids`, optional `followed_by_ids`, `on_death` | A removal of the mechanic's aura from a player when one of `ability_ids` hit or landed on that player within 200 ms of it | The player; with `on_death`, a removal within 200 ms of the player's death also counts, charged to no one |

`aura_removed_by` is for shells and shields the raid must not break. `ability_ids` may be other mechanics' IDs. With
`followed_by_ids`, one of those auras must also open on any player within 200 ms after the removal, confirming
the break. `ability_ids`, `followed_by_ids` and `on_death` are allowed on this rule only.

## Casts

| Rule | Params | A failure is | Charged to |
|---|---|---|---|
| `not_interrupted` | none | An enemy `cast` of the mechanic with no interrupt of it in the 100 ms up to the cast | No one, until raid-plan assignments exist |

## Not expressible yet

Open requests from authored files are in `docs/encounter/follow-ups/`, under each encounter. The recurring ones: a grace
window on `hit_without_aura`, rules limited to one phase, aura-application versions of `hits_on_non_target`
(a stun that deals no damage), and position rules (who stood where at a hit).
