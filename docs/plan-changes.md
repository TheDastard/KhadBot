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
