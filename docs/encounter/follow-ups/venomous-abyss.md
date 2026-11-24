# The Venomous Abyss follow-ups

Open work on The Venomous Abyss's encounter files (`config/encounters/53-the_venomous_abyss/`) and its game
art. One heading per encounter; the encounter file's header comment points to it. The rules for items are
the same as for `docs/follow-ups.md` (`.claude/rules/docs.md`).

## The Venomous Abyss, game art

- **The client export stops short.** Three runs of `exportInterfaceFiles art` on client 12.1.5.70783 wrote every
  journal file up to file ID 7966637 and none of the ten after it, Kith'ix's `ui-ej-boss-kithix.blp` (8214195) among
  them, likely a timeout. New bosses' portraits will be missing until the export completes; find a way to make it
  finish (or export the newest files) before the next raid tier.
- **Hosted buckets.** The portraits are uploaded to the local stack only; staging and production need the upload
  script run against their buckets (`docs/game-art.md`, "Uploading to a hosted bucket"), and the Blizzard client ID
  and secret in their secrets. The upload folder must also hold Kith'ix's `UI-EJ-BOSS-Kithix.blp`, which the export
  lacks.

## Ula'tek (3492), Heroic, Normal

`config/encounters/53-the_venomous_abyss/3492-ulatek.yaml`, awaiting review; Mythic isn't covered. Every pull has
coverage `full` and no `phase_trigger_unmatched`. Failures over the 47 Heroic pulls: Mother's Wrath 13, Unchecked
Rage 21, Rattler Slam 10, Calcified Corpse 19, Circling Prey 12, Desperate Thrash 28, Acidic Expulsion 1; over the
8 Normal pulls (`1DaN32nvrTFZKBqx` 34-41): Mother's Wrath 6, Unchecked Rage 17, Rattler Slam 2, Calcified Corpse 4,
Desperate Thrash 7, Deadly Venom 69, Toxin Cloud 18.

- **Review the failure rules** against those counts. On Normal pull 41 one Deadly Venom cluster fails 17 of 27
  players and the one Toxin Cloud 18 of 27, just before stage two: close to firing on everyone.
- **Damage windows.** Only Rage of the Shackled is one; Spectral Coils (every player hit, many overkill deaths)
  could be. Rage's `expected_defensive_cooldowns: 1` is a guess, and its cast (1286860) and the Venomous Heart's
  (1299526) aren't listed, so enemy Casts miss them; adding them refetches enemy Casts for every pull.
- **Poisonous Bite** has no rule until a source says how long the DoT may stay (`debuff_duration_gt`).
- **Soul Constrictor soakers** (Heroic) are undetected: the debuff is never reapplied and every hit is equal
  (`docs/encounter/failure-rules.md`). Needs positions at impact or a raid plan.
- **Mephitic Thrash** has no rule; positions could judge it once a reviewer confirms the radius and shape.
- **Mother's Wrath `reach_yd`.** Target distance doesn't split the 13 Heroic failures cleanly; is it the right test?
- **Hits with no enemy cast.** One Circling Prey has none; rules on Ula'tek's casts may change only who is charged.
- **Ingested Venom** (1313529) as the Serpent's Bite soak debuff is the raid's belief; confirm.
- **Unseen on Normal**: Fury Unleashed, Poisonous Bite, Petrifying Sting, a close-in Circling Prey (1M is Heroic's),
  Acidic Expulsion past 1 stack, an uncut Malice.
- **Unowned punishments.** 2 Unchecked Rages and 3 Rattler Slams have no aggro holder; they need tank assignments.

## The Coiled Altar (3429), Heroic, Normal

`config/encounters/53-the_venomous_abyss/3429-the_coiled_altar.yaml`: first pass, 35 Heroic and 13 Normal pulls.

- **Review the failure rules**: Volatile Venom bystanders (Heroic 701, Normal 452), Axegrinder (568, 337), Normal's
  Despair (123, a ghost reaching its player). `MECHANIC_CAP` (8) cuts failing mechanics from 6 Heroic digests.
- **Player-on-player damage**: 104.8M over the 32 pulls that reach Dreadmarch is unclassified, mostly players' DoTs
  (451963, Frost Fever, Virulent Plague, Flame Shock); none hits a player under Dreadmarch. Name the cause.
- **Guillotine soak threshold** (5, guides): Execution follows soaks of 2 or fewer on Heroic, 4 on Normal (no soak
  debuff there); one Grim Execution has no soak hit. A `hit_while_aura` rule needs a second soak.
- **Tanks**: Normal Sever-stack failures are all in 6xHBTkhGXbL3CdMD (16; a stack outlasts 38 s, 33 s elsewhere).
  Lethal Sever 1301681 (Normal) is in no entry. Soul Sever needs Malacrass's tank (taunts, Dread Bolt, raid plan).
- **Gloombomb bystanders** need a grace window for `hit_without_aura`. Heroic-only: Grim Guillotine, Soulcoiler.
- **Confirm**: Noxious Ground's free tick, Toxic Deluge as avoidable, Fangs' `expected_defensive_cooldowns: 1`, and
  Unworthy's (1312424) trigger. No source yet for Venomfang, Venom Rupture, Corrupted Toxin, Dreadmarch rules.

## Kith'ix (3513), Heroic

`config/encounters/53-the_venomous_abyss/3513-kithix.yaml`, first pass awaiting review; five Heroic pulls, one kill.

- **The intermission isn't a phase.** Suffocating Darkness repeats and ends on Liadrin's Divine Radiance, not a boss
  event, so it sits inside `p1`.
- **Judgment calls to confirm**: Extinguish when a tank survives it by a planned immunity or defensive, Dark
  Devastation's 8 s window, Digestive Juices' 3-stack limit (a 3-stack set also wiped), Voidscar hits on the tanks
  placing it, and Overwhelming Fear as a full 6 s rather than an early dispel (needs positions).
- **Suffocation double counts** an unhealed Abyssal Grasp (9 of 21). Stage Two rests on one pull: Null Gate rules
  and Nullify's lethal stack (4 to 6 by guide) are unconfirmed; Hatch, Mindsting and Dark Frenzy never appear.
- **Need a source**: Blood of the Old Ones (1308670, 6 killing blows), Nightfall's stack limit, and Eightfold
  Eclipse's `expected_defensive_cooldowns: 1`; Eclipse Fragment soaks need a raid plan.

## Nek'zali the Soulcoiler (3470), Heroic, Normal

`config/encounters/53-the_venomous_abyss/3470-nekzali_the_soulcoiler.yaml`; 7 Heroic pulls (3 kills), 2 Normal kills.

- **Review the failure rules**; Latent Cultist (299 Heroic contacts, 81 and 53 per Normal kill): is one tick a failure?
- **Log-derived limits to confirm**: Ritual Burn past 5 (a six-Invoke Stage Two passes it with no Amani in the
  well), Hollowing Strikes past 15 (guides swap at 6 to 8; 2 and 1 runs per Normal kill), Hungering Pyre under 6
  soakers (should scale with size; untested on Normal, where every Pyre had 12 to 14).
- **Restless Amani reaching the well** has no rule (it logs only a Soulcoil Rite, 1288772: count Rites with no
  listed cast, charged to no one). Normal lacks Ritual Burn, Cremation, Vessel of Awakening and Uncoiled Rage.
- **Possession Barrage** early bursts need positions (its defensive count is a guess); **Soulcoil Well** counts
  include raids walking in to end a lost pull, which the wipe cutoff doesn't always catch.
- **Confirm** the intermission starting on Ritual of Awakening (WCL's transition is 23 s later on Heroic, 21.4 on
  Normal). No rule yet for Latent Cultist emergence, Essence Rend placement (positions) or Corpse Blight stacks.

## Entombed Sentinels (3445), Heroic, Normal

`config/encounters/53-the_venomous_abyss/3445-entombed_sentinels.yaml`, first pass awaiting review. Failures, Heroic:
Blood Venom 549, Living Venom 287, Deadly Venom 60, Burst 22, Miasma 9, Noxious Blast 9, Injection 2; Normal (2 kills):
Noxious Blast 6, Miasma 1, Deadly Venom 1 (no damage); no Living or Blood Venom or Burst (toxins cleared).

- **Review the thresholds**: Blood Venom `hits_per_cluster_gt 1` (is crossing pools part of the fight?) and Miasma
  `soak_count_lt 8`, Heroic-derived (Normal splits ~3M, not 4.4M; its one failure, 4 hit, killed 3); needs group sizes.
- **Unowned failures.** Noxious Blast needs droplet assignments; no blame option names Injection's debuffed tank. No
  rule yet for Toxic Droplets, Clinging Murk, Blighted Blood (dispel timing disputed) and the marks (no limit given).
- **Not in the logs**: Empowering Slam's bonus, Living Venom's trigger, Ula'tek's Dominance (testable from positions
  once its range is known), Helical Toxins' starting stacks. Contaminate's `expected_defensive_cooldowns: 1` is a guess.

## Nymrissa Wavecaller (3379), Heroic, Normal

`config/encounters/53-the_venomous_abyss/3379-nymrissa_wavecaller.yaml`, first pass awaiting review, from one Heroic
and two Normal kills; recheck on wipes and the Mythic pulls. Normal failures: Whirlpools 25 and 5, Bites 4 and 0.

- **Unseen in any kill, so no IDs**: Bubblefin Berserkers and their Pulsing Tides (a murloc reaching the bubble has
  no rule), Shatter, Wavecaller's Might and Unending Tides. Pulsing Tides and Shatter fit `per_cluster` by nobody.
- **No rule yet** for Frost Orb stacks (Heroic `debuff_stacks_gt 2` gives 4: is 3 the limit? Normal orbs refresh,
  never stack) or Lingering Frost, which needs to excuse the soaker's own patch or a tick limit.
- **Blame**: Iceblade Flurry past 6 stacks (Heroic 1, Normal 4 and 1) charges no one, though the other tank should
  have taunted; whether Wild Bite on a tank knocked into the water is that tank's error is for a reviewer.
- **Guesses**: Abyssal Rain's and Pop!'s defensive counts, and what Pop!'s zero hit marks. Repeating phases on the
  Alluring Bubble and Swirling Whirlpools casts could split the single phase's ~110 s cycle.

## Vashnik the Malignant (3455), Heroic, Normal

`config/encounters/53-the_venomous_abyss/3455-vashnik_the_malignant.yaml`, first pass awaiting review, from three Heroic
and two Normal kills. Failures (Heroic; Normal by kill): Froth 177; 119, 60, Wave 221; 147, 122, Burst 29; 61, 9.

- **Plague Froth and Wave may overcount.** 60 Heroic Froth failures are first-tick bystanders (no grace); Normal Froth
  fails 24 and 20 of 25 players, Wave all 25 in both kills. Wave (healed through) may not be a failure at all.
- **Stygian Burst blame.** Carriers in melee hit the tanks repeatedly, so the carrier may be at fault rather than the
  player hit; a former carrier hit within 1.5 s of the infection ending also counts.
- **Staggered dispels and add kills** (Exploding Infection, Burning Venom deaths) need a rule kind for two occurrences
  closer than a limit; Caustic Surge has no stack counts (nor a DoT on Normal), so `debuff_stacks_gt` can't see it.
- **Unconfirmed**: unsoaked Bile (1282616), Virulent Fumes and Congealing Bolt (not in the Journal), Imbibe's cooldown
  count, how Normal's Siphon Blood picks targets, infected players per Imbibe (2 to 6 on Normal). Unseen: Blood fountain
  on Heroic, Clotting Venom damage and Malignant Burst on both, Malignant Catalyst and Catalytic Bile on Normal.

## The Lost Explorers (3497), Heroic

`config/encounters/53-the_venomous_abyss/3497-the_lost_explorers.yaml`, first pass awaiting review, from only four
Heroic pulls (three kills); counts and the unseen abilities are in its comments.

- **Phases assume the feed order Nema, Epu, Gebbo.** Another order gets wrong phases, so the format needs phases
  that match in any order; no pull reached the fourth Final Ascension, so the last phase is missing.
- **Review**: Shell Spin charges the cast's target (7 of 32 casts); Aftershock `hits_per_cluster_gt 0`, Shredding
  Shards past 7 stacks, Blink Nova's defensive count. Relic Rupture charges no one until a raid plan assigns crates.
- **No rule yet** for Mighty Thud (no failed leap seen, and not a damage window), Steady Strikes, Splinters and
  Spreading Flames; Elemental Explosion's rule is untested on a debuff carried into the next volley.
- **Needs positions or more logs**: Blink Nova placement, United Defense, Fishy Feedback's damage, Haunting Spirits
  (1310561, unexplained deaths in the wipe).

## Sszorak (3420), Heroic, Normal

`config/encounters/53-the_venomous_abyss/3420-sszorak.yaml`, first pass awaiting review, from 24 Heroic pulls (three
kills) and 4 Normal pulls (two kills).

- **Tempest charges nearly everyone**: `aura_applied` gives 15 to 24 players a pull on Heroic, 23 to 25 on Normal.
  Decide whether every contact counts or only repeated ones (`debuff_stacks_gt`; stacks reach 2 to 7 a pull).
- **An early Viscous Cyst pop** has no rule: it needs a phase-scoped mechanic (per_cluster, no blame: no one is logged).
- **Mutilate double soaks and Crosswinds bystanders** have no rule: the gash opens in the hit's ms and a mark ends up to
  77 ms before its hit (needs an aura age or grace window); on Normal most players soak both Mutilates of a pair.
- **Review** Ravage: WCL writes a targeted cast row per player hit, so the tank may be charged (1 to 8 a Normal pull).
- **Review** Turbulent Gusts' 4 s limit (an immune partner?); 36 of 261 Normal debuffs end at 3.45 to 3.51 s.
- **Not covered:** cyst placement (positions; Surge's `expected_defensive_cooldowns: 1` is a guess) and intermission
  blow-offs (no log row). Caustic Claws and Residue (in one Heroic report) have no Normal rows, so stay Heroic-only.

## The Twin Fangs (3421), Heroic, Normal

`config/encounters/53-the_venomous_abyss/3421-the_twin_fangs.yaml`, first pass awaiting review, from 12 Heroic pulls
(three kills) and 7 Normal pulls (two kills).

- **Ravenous Feast double soaks** (Heroic) include planned immunity soaks and a tank's survived double soaks (needs
  a hit-type filter or raid plan); undersoaks need a soak-count field. Normal has no rule: the raid soaks together.
- **Review the thresholds.** Coiling Ichor has no rule (needs positions); Vile Flood counts every tick (`value: 1`
  would allow the early crossing guides advise); tanks may not dodge Stir the Depths, Deadly Venom and Vile Flood.
- **Out-of-reach hits** (Spittle, Bolt) also charge non-tanks the bosses turned to as the raid died; unsoaked globules
  and Stone Breakers (14 on Normal) go to no one until a raid plan. Normal's 10-stack bursts (1303196) are uncounted.
- **Unchecked:** Stone Breaker's stack rule fired once (Normal); no Submerge start before Sanguine Storm, Uncoiled
  Wrath, Caustic Rain or Normal Sanguine Storm pools in the logs; Flames of Ula'tek (1310287, from Vexhul) on 2 Normal
  wipes is unexplained (17.6% of RzFgvG7xV1AHLXh6 9's damage taken: coverage partial); Stir's cooldown count is a guess.
