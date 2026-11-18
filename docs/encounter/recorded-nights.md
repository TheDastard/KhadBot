# What the recorded nights show

Evidence from the three recorded Heroic Ula'tek nights (`H7z1fqDXjRTFnbtg`, `QkVa6GHRK8yfrTbw`,
`trMgApZCVYkv3jdq`; 62 boss pulls, 47 of them Ula'tek) that encounter files and rules rest on. The API behaviour
behind it is in `docs/wcl/`. Read it before changing a rule these findings justify.

## Findings from the real reports

From the fight lists of the three reports and the first live ingest.

- **Raid size for the wipe cutoff stays `len(pull.friendly_player_ids)`.** In all 62 recorded pulls the
  friendly players are exactly the players with events, including the Mythic Nymrissa pulls where WCL reports
  `size=20` but 22 played (Lair fights allow 15 to 25 on Mythic). Never use WCL `size` for anything. Plan 6.3
  should say `raid_size` is the pull's friendly-player count.
- **`lastPhase` is an ordinal, not a phase ID.** WCL numbers stages and intermissions separately, so a pull
  ending in Ula'tek's intermission (phase ID 3) reports `lastPhase=1`. The `phase_trigger_unmatched` anomaly
  compares detected phases with `Pull.last_phase`; switch it to the last `phase_transitions` ID. Phase detection
  itself already uses transitions or triggers.
- **Keep both progress figures.** `Pull.boss_pct` stores `fightPercentage`. Add `bossPercentage` as a separate
  field (`boss_hp_pct`) and keep `fightPercentage` for "how close was the pull"; they differ by 10+ points in
  later phases. Contract change (plan 6.1 `Pull`, 6.4 digest). *Implemented 2026-10-11*: `GET_REPORT_META`
  requests `bossPercentage`; `Pull.boss_hp_pct` and `PullDigest.boss_hp_pct` default to None so `pulls.json`
  written before the change still loads; the `pull` ref, `list_pulls` and `compare_pulls(metric="phase_reached")`
  carry it. The rendered digest header reads `fight 35.0% | boss HP 41.0%` (was `boss 35.0%`, which was fight
  progress). The analyst's multi-pull context and the `list_pulls` / `compare_pulls` docstrings called
  `boss_pct` "boss health"; they now name both figures, which changes the analyst prompt version. Ingest now
  rewrites a pull whose metadata changed even when its `events_hash` did not, so a field added to the report
  query reaches stored pulls. The recorded `report.json` files (fixtures and `KHADBOT_DATA_DIR`) had
  `bossPercentage` merged in from a live `fights { id bossPercentage }` query; nothing else in them changed.
- **Rosters come from each pull.** Report-wide player actors include everyone the logger saw (276 in
  `H7z1fqDXjRTFnbtg`). Anything that lists "the raid" must use the pull's friendly players, never
  `masterData.actors`.
- **A report can mix bosses and difficulties.** `H7z1fqDXjRTFnbtg` has three Heroic bosses and Mythic Nymrissa.
  Encounter-file lookup is per `encounter_id` already; digests and reviews must not assume one boss per report.
- **Phase names.** `report.phases` maps phase IDs to names and intermission flags; the Ula'tek table is in
  `docs/wcl/report-data.md`. Players count stages, so "phase 3" on Ula'tek is phase ID 4 (Stage Three), which started 362
  to 365 s into every pull that reached it. Use these IDs in `config/encounters/53-the_venomous_abyss/3492-ulatek.yaml`; a `wcl_phase` trigger
  works for every Ula'tek phase. The enrage is *Fury Unleashed* (confirmed by the raid leader).

## Log investigations

Three questions answered from the recorded Heroic Ula'tek pulls (47 pulls, three nights), read from the raw
pages with scratch scripts and no WCL calls. The API behaviour behind them is in `docs/wcl/` (Events, and
Instances and positions). The proposed contract text is in `docs/plan-changes.md` under
`encounter/spike-logs`.

### Enemy casts near the end of a pull: the boss stops casting, the log doesn't lose them

Measurements:

- **Only `H7z1fqDXjRTFnbtg:15` reached the enrage.** It is the one pull of the 47 with Fury Unleashed damage.
  Its last enemy cast is at 573.7 s of 618.0 s: Ula'tek finishing Rage of the Shackled (cast 1286860, begun at
  567.2 s) and the Venomous Heart casting Venomous Heart (1299526) in the same millisecond.
- **Rage of the Shackled is a 20 s channel, and Ula'tek casts nothing while channeling.** Its pulses (1307367)
  start 0 to 54 ms after the cast and the last one lands 19.97 to 20.06 s after it. Across all 55 channels,
  Ula'tek has no cast or begincast during the channel. On `H7z1fqDXjRTFnbtg:15` it runs from 573.7 to 593.7 s.
- **After the channel, two hits have no cast.** Circling Prey damage at 594.6 s (13 hits, 5 deaths) has no
  Circling Prey cast in the 52 s before it, the only one of the 17 Circling Prey occurrences without its cast
  0.1 to 1.2 s earlier. Fury Unleashed starts at 598.8 s (3 hits of 1286905, then 1295004 every second to
  617.8 s), and neither Fury Unleashed ID is ever cast. With one enrage on record, the logs can't tell whether
  the enrage is never cast or its cast was lost.
- **Ula'tek's other events stop too.** After 598.8 s its only events in any data type are Fury Unleashed and
  Necrotic Vapors damage. Its last Threat swing is at 595.4 s and its last debuff on a player at 595.0 s. At
  least two players were still alive until 617.8 s.
- **The filtered enemy Casts page misses the channel.** The Casts filter is the encounter file's ability IDs,
  and the Ula'tek file lists only the pulse (1307367), not the cast (1286860) or the Venomous Heart's cast. Casts
  that appear only in the unfiltered pages: Writhing Gestation (Blightscale Clutch 208, Doomscale Warden 60),
  Call of the Serpent (1304012 64, 1300751 37), Shadow Molt 64, Rage of the Shackled 55, Venomous Heart 55,
  Defect: Weakened 40, three Submerge IDs (60 in all).
- **The same channel ends other pulls.** The kill `H7z1fqDXjRTFnbtg:20` starts it at 573.7 s and ends 5.5 s
  later, inside it. `QkVa6GHRK8yfrTbw:22` starts one at 129.0 s and wipes at 158.0 s. Every other boss pull on
  the three nights, on every boss, has an enemy cast in its last 10 s.

What this means for rules:

- A missing enemy cast is never evidence that a mechanic didn't happen. `cast_target` blame then finds no one,
  and `per_cluster` still counts the occurrence (as it does today).
- Detect the enrage from its first damage (1286905), never from a cast.
- Whether the file should list the channel's cast (1286860) is left to the Ula'tek review (see
  `docs/follow-ups.md`); adding it changes the Casts filter, so every pull's enemy Casts would be refetched.

### Viper spawns: charge the carrier whose egg broke on Caustic Waves, with a new rule on the shell

Measurements:

- **A Blightscale egg is not an actor.** Its only trace is the Malignant Shell debuff (1295360, applied by
  Ula'tek) on the player carrying it, plus that debuff's damage (1297213). The debuff's removal is the only
  event at a hatch. The Doomscale Egg (game ID 268164) is a different egg and is an actor: it begins casting
  Hatching Doom (1306862).
- **A Viper's first event** is an Acidic Burst begincast (52 of 153 Vipers), a Threat melee swing (46), melee
  damage (20), or a player's cast aimed at it. It comes 0.5, 2.2 and 5.5 s (10th, 50th and 90th percentile)
  after the nearest shell removal before it.
- **What happens to the carrier at the removal decides the add.** Each of the 1,235 removals, classified by what
  happened to the carrier in the 200 ms after it:

  | At the removal | Removals | A Viper appears within 8 s | A Rawling appears within 8 s |
  |---|---|---|---|
  | Spectral Coils hit (1287265) | 747 | 60 | 746 |
  | Carrier's death | 125 | 101 | 42 |
  | Caustic Waves hit (1292403, Ula'tek's wave) | 29 | 29 | 0 |
  | Another hit | 99 | 17 | 88 |
  | Nothing | 235 | 19 | 230 |

  The counts aren't one-to-one: several removals often land together and each one sees every add that appears
  after it. A shell broken by Spectral Coils hatches Rawlings; one broken by Caustic Waves always hatched a
  Viper; the carrier's death usually did (17 of the 23 deaths to Spectral Coils itself were followed by a
  Viper). No shell came off at a hit from the other Caustic Waves IDs or at a Deadly Venom damage tick; the
  venom pools break shells through their debuff landing instead (see "Viper hatches" below).
- **153 Vipers (by instance) on 30 of the 47 pulls.** 122 have a removal at a Caustic Waves hit or a death in
  the 8 s before, 2 only other removals, and 29 have no shell removal at all in that time. Those 29 come in
  groups at 198 to 262 s and 395 to 468 s (for example `QkVa6GHRK8yfrTbw:20` at 395.6 to 399.3 s and
  `trMgApZCVYkv3jdq:16` at 396.4 to 400.0 s). Nothing fetched shows their cause: there is no egg actor, no cast
  and no debuff change.
- **Positions don't sharpen the link.** The carrier's last known position and the Viper's first one are a median
  of 660 to 760 units apart, depending on the cause (Ula'tek's melee swings land at 1,336 to 2,026).

This led to the `aura_removed_by` rule; the next entry has the measurements it was built on.

### Viper hatches: a shell break confirmed by Putrid Membrane, charged to the carrier unless they died

Measurements, on the same 47 pulls:

- **Putrid Membrane marks every hatch.** Each new Viper puts Putrid Membrane (1301268, source Ula'tek, since
  the egg is no actor) on the raid: an `applydebuff` on about 20 players for the first Viper, then a stack or
  refresh for each one after (968 applies, 1,606 stacks, 1,084 refreshes). Its rows come 0 to 82 ms after the
  shell removal that hatched the Viper, about 3 s before the Viper's own first event.
- **Two things break a shell on a living carrier.** A Caustic Waves hit (1292403) lands 0 to 53 ms after the
  removal (33 removals), and the Deadly Venom debuff (1297338, the venom pools) lands 0 to 98 ms before it (13).
  All 46 were followed by Putrid Membrane. Deadly Venom first ticks about 1 s after its debuff lands, and 4 of
  the 13 carriers stepped out before any tick, so the debuff landing is the signal, not the tick.
- **Deaths hatch Vipers too.** 113 removals at the carrier's death (within 200 ms) were followed by Putrid
  Membrane. The killing blows: Mother's Wrath 41, Spectral Coils 16, Putrid Membrane 13, Malignant Shell 12,
  melee 9, Rattler Slam 8, Necrotic Vapors 7, others 7. Putrid Membrane and the shell's own tick killing a
  carrier is a chain: one Viper's dot kills a carrier, whose egg hatches the next.

Decision: a failure rule `aura_removed_by {ability_ids, followed_by_ids, on_death}`, on `malignant_shell` as
`{ability_ids: [1292403, 1297338], followed_by_ids: [1301268], on_death: true}`. A removal of the mechanic's
aura from a player is a failure when one of `ability_ids` hits that player or an aura from it lands on them
within 200 ms either side, charged to that player. With `on_death`, a removal within 200 ms of the player's death
is also a failure, charged to no one; a break by `ability_ids` wins over a death. With `followed_by_ids`, either
kind counts only when one of those auras opens on any player within 200 ms after the removal. That gives 46
charged failures and 113 uncharged ones on 33 of the 47 pulls. Why this shape:

- **A rule on the shell, not a blame spec on the Viper.** A blame spec only charges `per_cluster` occurrences,
  and a Viper's damage forms many clusters over its life, not one per hatch. The removal row is one event that
  names the carrier and the moment. Putrid Membrane only confirms that the break hatched something.
- **A death hatch counts but isn't charged.** The death is already charged by the mechanic that killed the
  carrier, so charging the egg too would count one mistake twice. It still counts, because the Viper turns one
  death into a threat to the raid, and the uncharged failure shows that in the mechanic's failure count.
- **One rule with an option, not an `aura_removed_by_death` rule.** A mechanic has one failure rule, so a
  second rule couldn't sit on the shell beside the first.
- **Vipers with no shell removal before them stay uncharged** (29 of the 153 in the entry above), because
  nothing fetched shows their cause.

### Wrong-target damage: fetch DamageDone for amounts, and don't fetch the Enemies side of Threat

Measurements, from the data already fetched:

- **The Venomous Heart (game ID 267460) appears only through casts.** It casts Venomous Heart (1299526) once
  per Rage of the Shackled: 55 casts for 55 channels, 52 of them within 50 ms of the channel's cast. It deals no
  damage, never swings, and has no death in the Threat data, so its lifetime has to come from the channel.
- **Where players aimed during the channels.** Players' casts aimed at an NPC hit the Venomous Heart 13,936
  times and Ula'tek 755 times (plus 117 on Rawlings; 8,409 casts had no target). Of the 938 player-channels with
  at least 5 aimed casts, 13 put half or more of them on Ula'tek.

The Enemies Threat events (about 12.7k events and 4.9 MB per long pull) and the 551 kB table view only say who
aimed at which NPC, never how much damage landed. So DamageDone is needed either way, and its rows already name
the NPC and instance they hit, which is all a wrong-target rule judges. Player spells' aim is already in the
friendly Casts data, as above. What Threat would add is the aim of auto-attacks and pets, which tells a cleave
from a wrong target. That isn't worth 4.9 MB per pull before the simple rule exists, and the table view would
also need its own query and storage format. The proposal (a `kill_targets` block in the encounter file, an
`off_target_share_gt` rule and a `KILL TARGETS` digest section) is in `docs/plan-changes.md`. Before it's
built, someone with WCL credentials has to check DamageDone on the live schema: its fields, page count and size
on one long pull, recorded in `docs/wcl/report-data.md`.

## Friendly damage on the recorded nights

Measured on all 62 recorded pulls, read from `KHADBOT_DATA_DIR` without writing to it.

- **Ula'tek's unregistered share was all player damage.** Before the registry it was 0.3% to 3.3% per pull (mean
  1.7%) on the 47 Heroic pulls; after it, 0.0% on every pull, with coverage `full` throughout. Friendly damage
  is now 0.4% to 3.2% of damage taken. The three encounters without a file stay at coverage `none`; their
  unregistered share drops by the same friendly share (Nymrissa 98.8% mean, The Coiled Altar 97.9%, Nek'zali
  99.0%).
- **Twelve abilities, all from the damage rows.** On Ula'tek, by damage over the 47 pulls: Refraction (1309786,
  117M), Spirit Link (98021, 59M, dealt by the totem, a pet), Time Dilation (361029, 44M), Burning Rush (111400,
  28M), Seriously Sharp Seashell (1292299, 13M), Tempered in Battle (469704, 12M), Fel Armor (387846, 10M), Set
  Fire to the Pain (453286, 7M), Rune of Void-Tainted Shell (1287955, 3M), Stretch Time (413924, 75k). Blessing
  of Sacrifice (6940) appears on Nek'zali and The Coiled Altar. The classes of Spirit Link, Tempered in Battle,
  Time Dilation, Stretch Time and Burning Rush come from the follow-up that proposed the registry. The maintainer
  corrected the rest and confirmed every entry (2026-10-18): Time Dilation (an Evoker ability), Refraction and Set
  Fire to the Pain take a share of damage as a DoT, so they are delayed boss damage; Seriously Sharp Seashell is a
  trinket proc the player can't control, so it gets its own `involuntary` class and stays out of the per-player
  self-damage list and death windows; Rune of Void-Tainted Shell is a borrowed power, an automatic shield that
  leaves a DoT when the player takes damage, so it is delayed boss damage too. All twelve entries are
  `verified: true`; a future unverified entry that deals damage is flagged `friendly_damage_unverified:<id>`. Across
  the 47 Ula'tek pulls friendly damage splits into 181M delayed boss, 71M redistribution, 29M self-inflicted
  and 13M involuntary.
- **Shadow Word: Death has no friendly source.** Its 32409 backlash (1.6M on Ula'tek) is recorded with source
  -1 (Environment), so `source_friendly` never catches it. The registry matches entries by ability ID whatever
  the source, and the entry moved there from the Ula'tek file's `ignored_abilities` (`file_version` 4).
- **The Coiled Altar has player-on-player damage that isn't self-damage.** Over its 7 pulls, 29M of ordinary
  class spells and pet attacks (Frost Fever, Felseeker, Moonfire, Wicked Cleave and others) hit other players,
  which looks like a mind-control mechanic. They stay out of the registry and are listed as unclassified
  friendly damage. Coverage
  there is `none` anyway; once that encounter has a file, its mechanic should name them, since an
  encounter-file ability is never unclassified. With an encounter file, unclassified friendly damage at or above
  the 10% coverage threshold raises `unclassified_friendly_damage:<pct>`, because it no longer lowers coverage.
- **Digest size.** Rendered digests across the 62 pulls are 439 to 2,225 tokens with the new SELF DAMAGE
  section, inside the 3k ceiling.
