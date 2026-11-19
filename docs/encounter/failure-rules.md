# Failure rules

How annotation decides which rows of a mechanic are failures and who is charged with each.

Annotation adds a `blamed_id` column, the player charged with each failure row. `MechanicStat.failures_by_player`
and `MechanicCluster.failed_players` count `blamed_id`, and an `event:` ref on a failure row adds `blamed` (a name).

Failures are charged by one of three kinds of evidence:

1. **The player hit**: `any_hit`, `hits_per_cluster_gt`, `hit_amount_gt`, `aura_applied`, `debuff_duration_gt`,
   `hit_while_aura`, `hit_without_aura`, `hits_on_non_target`.
2. **No one**: `not_interrupted` (the enemy cast row), `soak_count_lt` (the cluster's first row),
   `debuff_stacks_gt`, until raid-plan assignments exist; and `per_cluster` with `blame: {by: nobody}`, for
   occurrences that are the whole raid's failure.
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
  - `{by: nobody}` charges no one and records no blame context; it takes no other key.
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
  here and on `hit_without_aura`, and rejected elsewhere.
- **`hit_without_aura {aura_ids}`**: the reverse: each player hit by the mechanic's damage while carrying none of
  the listed auras, with the same aura spans. There is no grace window, so damage landing just after the aura's
  remove counts as unprotected.
- **`hits_on_non_target {ms}`**: per cluster, the latest completed enemy `cast` of one of the mechanic's own
  `ability_ids` with a friendly target, in the `ms` up to the cluster's first hit, names the target; every other
  player hit is a failure, judged once per cluster. `begincast` rows carry no target and don't count, and a
  cluster with no such cast is not judged. The completed cast keeps its target through a taunt during the cast.
