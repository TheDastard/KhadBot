# Legacy encounter analysis vs the pull digest

This compares the legacy encounter tool (`tools/warcraftlogs/encounter.py`, `fetch_encounter_events` and its
`_analyze_deaths`, `_analyze_avoidable`, `_analyze_cooldowns` and `_analyze_healing` passes) with the digest
records built by `encounter/digest.py` (`build_pull_records`, uncapped) on the seven recorded fixture pulls. It
lists every difference and whether it is intended. The digest must not reproduce a legacy defect to make the two
agree.

Run on 2026-10-11.

## Method

- **Legacy side**: `fetch_encounter_events(code, fight)` run live against WarcraftLogs for each fixture pull,
  unmodified, with every GraphQL response captured so the inputs to each pass could be inspected. 7 pulls cost
  173 points (about 25 per pull, against about 14 per pull for a full ingest; see `docs/wcl-api.md`). The
  reports are finished logs, so the live data is the data the fixtures were recorded from.
- **Digest side**: the fixture pulls ingested offline (`tests/helpers/encounter/real_fixtures.py`), then
  `build_pull_records` with the authored encounter files and cooldown registry under `config/`. The registry's
  spell IDs are exactly the legacy `MAJOR_COOLDOWN_IDS`, so both sides track the same 17 cooldowns.
- Deaths are matched by player name and time (within 50 ms). The comparison scripts were one-off and are not
  kept: the legacy tool is deleted when the conversation agent replaces it.

Pulls: `H7z1fqDXjRTFnbtg` fights 10, 15, 20 (Heroic Ula'tek) and 23 (Mythic Nymrissa Wavecaller, no encounter
file); `QkVa6GHRK8yfrTbw` fight 14; `trMgApZCVYkv3jdq` fights 11 and 18 (all Heroic Ula'tek). What each pull is
for is in `tests/fixtures/wcl/README.md`.

## Results per pull

"L" is legacy, "D" is the digest. Window damage sums each player's first death only, because those are the
deaths both sides have a window for. The damage-taken totals compare the legacy damage-taken-by-ability table
with the digest's friendly damage events (`amount`, and `amount + absorbed`).

| Pull | Deaths L / D (matched) | Pre-death window damage L / D | Damage taken L / D `amount` / D `amount + absorbed` | Cooldown uses L / D | Legacy cast page covers | Legacy "healers" (healer specs) | Interrupts / dispels D |
|---|---|---|---|---|---|---|---|
| `H7z1fqDXjRTFnbtg:10` | 19 / 19 (19) | 0.14M / 27.2M | 57.4M / 36.3M / 54.9M | 0 / 0 | 32.6 of 37 s (not truncated) | 22 (4) | 0 / 1 |
| `H7z1fqDXjRTFnbtg:15` | 25 / 25 (25) | 0.18M / 39.2M | 984.3M / 823.9M / 982.6M | 0 / 22 | 77.6 of 618 s | 22 (4) | 11 / 15 |
| `H7z1fqDXjRTFnbtg:20` | 11 / 11 (11) | 0.12M / 13.5M | 951.4M / 802.9M / 960.6M | 0 / 14 | 77.8 of 579 s | 22 (4) | 11 / 22 |
| `H7z1fqDXjRTFnbtg:23` | 20 / 20 (20) | 0.08M / 24.0M | 173.8M / 132.2M / 168.2M | 0 / 0 | 79.0 of 124 s | 22 (4) | 0 / 0 |
| `QkVa6GHRK8yfrTbw:14` | 19 / 19 (19) | 0.24M / 27.6M | 155.2M / 119.2M / 154.0M | 0 / 1 | 84.4 of 106 s | 22 (4) | 0 / 1 |
| `trMgApZCVYkv3jdq:11` | 22 / 22 (22) | 0.25M / 27.1M | 376.3M / 313.3M / 377.2M | 0 / 9 | 80.8 of 244 s | 22 (4) | 2 / 9 |
| `trMgApZCVYkv3jdq:18` | 20 / 20 (20) | 0.08M / 29.3M | 617.0M / 518.9M / 620.0M | 0 / 10 | 80.1 of 398 s | 22 (4) | 7 / 20 |

## What agrees

- **Who died and when.** All 136 deaths match by player and time on every pull, including the 22-player
  Mythic pull and the six repeat deaths after battle resurrections.
- **What hurt the raid most.** The legacy table's five largest abilities on each Ula'tek pull are all authored
  in the Ula'tek file: mechanics (Necrotic Vapors, Spectral Coils, Rattler Slam, Mother's Wrath, Blight Vein,
  Malignant Shell, Mephitic Thrash, Volatile Purge, Acidic Expulsion, Acidic Burst under `blightscale_viper`),
  the Rage of the Shackled damage window, or the ignored `Melee`. On Nymrissa, which has no encounter file, the
  digest's unregistered list has the same abilities in the same order, except Swirling Whirlpools: legacy ranks
  it third, the digest sixth, because most of it was absorbed (3.0M `amount`, 5.2M absorbed) and the legacy
  table counts absorbed damage.
- **Damage-taken totals.** The legacy table's `total` tracks the digest's `amount + absorbed`: within 1% on five
  pulls, +3.4% and +4.5% on the two shortest (`H7z1fqDXjRTFnbtg:23` and `:10`). The small remainder was not
  investigated. The digest reports `amount` (after absorbs and overkill) on purpose; see the M2 notes in
  `docs/decisions.md`.

## Differences

Each difference is intended. The first group is the known legacy defects the redesign was meant to fix. The
second group is defects found by this comparison. The third group affects both sides and is still open.

### Known legacy defects, confirmed

- **Cast stream truncated.** `GET_COOLDOWN_CASTS` asks for one page of 2,000 casts and never follows
  `nextPageTimestamp`. On six of seven pulls that page ends 78 to 84 s into the pull, so on the 618 s pull
  the legacy timeline covers only the first 13% of the fight. The digest's ingest follows every page.
- **Repeat deaths reuse the first death's window.** The legacy fetch requests a window only for a player's
  first death (its comment says "final"), and `_analyze_deaths` attaches that same window to the second death.
  On `trMgApZCVYkv3jdq:11`, Mortamur's death at 235.1 s shows hits ending at 128.1 s, just before the first
  death at 128.6 s. The other five repeat deaths show either the first death's hits or nothing. The digest
  builds a window for every death and clips a second death's window to after the first.
- **"Avoidable damage" is all damage taken.** `AVOIDABLE_SPELL_IDS` is empty, so `_analyze_avoidable` returns
  every ability, from 15 to 36 per pull. The digest separates authored mechanics with failure rules, damage
  windows, ignored abilities and unregistered damage.
- **Healing pressure covers every player.** The healing table is fetched by source, so `_analyze_healing` lists
  all 22 players, of whom only 4 have a healer spec. Its "overwhelmed" label (under 15% overheal) lands on 7 to
  12 players per pull, mostly damage dealers. The digest drops healing pressure.
- **No interrupts or dispels.** The legacy tool has none. The digest's data has 0 to 11 interrupts and 0 to 22
  dispels per pull, which feed the interrupt and debuff failure rules.
- **Report metadata fetched on every call.** Each of the seven legacy calls fetched the report's fights again.
  Ingest caches `report.json` once per report.

### Legacy defects found by this comparison

These are not in the plan's list of known bugs. They come from the legacy passes reading fields that WarcraftLogs
event and table payloads do not have.

- **No cooldown is ever found.** `_analyze_cooldowns` reads the spell ID from `event["ability"]`, but cast events
  carry a top-level `abilityGameID` and no `ability` object, so every cast has spell ID 0 and none match the
  registry. Legacy reports 0 cooldown uses on every pull. With that fixed, the truncated page would still find
  only 0 or 1 per pull; the digest finds 0 to 22.
- **Pre-death windows hold only self-damage.** `GET_DAMAGE_TAKEN_EVENTS` with `targetID` set returned 480 events
  over the 130 legacy windows, and in every one the source and the target were the dying player (Burning Rush,
  Spirit Link and similar). None of the boss damage that killed anyone is in a legacy window: 0.08M to 0.25M per
  pull, against 13.5M to 39.2M in the digest's windows from the same 10 seconds. Ability names in the windows
  are all "Unknown" for the same reason as the cooldown bug.
- **Killing blow and overkill are always blank.** `_analyze_deaths` reads `event["ability"]["name"]` and
  `event["overkill"]`. Death events have neither, so every legacy death says "killed by Unknown (overkill: 0)".
  The digest takes overkill from the killing hit.
- **Ability table keys.** `_analyze_avoidable` reads the spell ID from `ability.id` or `abilityGameID`, but table
  entries use `guid`, so every entry has spell ID 0. Its `players_hit` is `len(sources)`, the number of NPCs
  that dealt the damage, not the number of players hit.

### Shared defect, still open

- **Killing ability name.** Every digest death names its killing ability "Unknown Ability" (ability ID 0). Death
  events carry `abilityGameID: 0` and the real ability in `killingAbilityGameID`, which normalize does not keep.
  The digest's top hits and overkill are correct; only the name is missing. The fix is tracked in
  `docs/follow-ups.md`. The real-fixture digest goldens record the current output and change when it is fixed.

## Conclusion

The digest agrees with the legacy tool where the legacy tool works (who died when, which abilities did the most
damage, total damage taken). Every other difference comes from a legacy defect: six known ones, confirmed on
real data, and four new ones that left the legacy cooldown timeline, pre-death windows, killing blows and
ability IDs empty on real reports. None of them is reproduced. The one defect both sides share, the killing
ability name, stays open in `docs/follow-ups.md`.
