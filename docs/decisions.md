# KhadBot decisions log

Source plan: <https://claude.ai/artifact/Q45H17JuH5EXK8oz17CzQE> (Encounter Review Refactor v0.3).

## Decisions

| # | Decision | Choice | Why | Date |
|---|----------|--------|-----|------|
| D1 | Mode dispatch | Deterministic `Session.mode` (`coach` / `encounter`), switched by `/mode`; no LLM router | Predictable, testable, no routing errors | 2026-10-10 |
| D2 | Legacy encounter code | Leave untouched until M5, then delete | Not reachable by any agent today (not in `coach.yaml`), so patching buys nothing | 2026-10-10 |
| D3 | Engineering principles | Record the principle changes here; updating the external doc is H7 | Doc is outside the repo | 2026-10-10 |
| D4 | Models per role | `get_llm(provider, *, role=)`; `LLM_PROVIDER_<ROLE>` and `<ROLE>_MODEL` env overrides | Analyst and conversation have different cost/quality needs | 2026-10-10 |
| D5 | Prompt versioning | `prompt_version` = SHA-256[:12] of prompts, tools, schemas, verify rules; `GRAPH_VERSION` manual | Cache keys invalidate when behaviour changes | 2026-10-10 |
| D6 | Ingest execution | Inline async with progress callback; resumable via raw page cache; idempotent via `events_hash` | No job queue needed for a CLI | 2026-10-10 |
| D7 | Event store | polars + parquet, one file per pull, behind an `EventStore` protocol (S2 may swap to DuckDB) | Cheap, columnar, no server | 2026-10-10 |
| D8 | Runtime | Python only; `.venv` runs 3.14, pinned by `.python-version`; `requires-python >=3.12,<3.15`; ruff and mypy target 3.12 (updated 2026-10-17, see D12) | Pinned so local, CI and every worktree build the same venv; linting against the oldest supported version keeps newer syntax out | 2026-10-10 |
| D9 | Local persistence | Everything under `KHADBOT_DATA_DIR` (default `.khadbot_data/`); SQLite for findings/plans; `AsyncSqliteSaver` for checkpoints (M5) | One gitignored directory | 2026-10-10 |
| D10 | Package location | One sub-package, `src/khadbot/encounter/`, grouped inside by the module layout rule in CLAUDE.md: a sub-package for three or more modules that form one stage, reached through one or two entry points, with internals nothing else imports | A folder only earns its place when it hides something; shared contracts stay at the parent level | 2026-10-11 |
| D11 | Analyst output | Analyst ends by calling a `submit_report` tool whose schema is `AnalysisReport` | Works across Groq, Ollama, Anthropic and fakes | 2026-10-10 |
| D12 | Tooling | One owner per job. uv owns the Python version (`.python-version`), `.venv` and Python packages, and stays a standalone install with `[tool.uv] required-version` as its floor. pnpm owns `web/node_modules`. mise pins Node and pnpm in `mise.toml` and runs every task (`mise run check` and so on), locally and in CI. The Makefile and Chocolatey `make` are gone. mise never manages Python or the venv: no `python` in `[tools]`, no `_.python.venv`; tasks reach `.venv` only through `uv run`. Tasks are single plain commands with env vars in `env`. Variants of one task share a `group:` prefix (`install:cli`, `test:unit`, `gen:contracts`, `clean:all`); bare `install` is the full dev install. | The Makefile forced `cmd.exe`, so CI and Git Bash couldn't run it, and Node and pnpm had no pin. mise over `just`: `just` is a fine task runner but can't pin Node and pnpm, which would stay a manual install. uv stays outside mise because two tools creating the venv is the failure to avoid, and `uv run` already syncs `.venv` against `uv.lock`. Plain commands behave the same in cmd, PowerShell and Git Bash. | 2026-10-17 |
| D13 | CI | One GitHub Actions workflow (`.github/workflows/check.yml`): the `check` job runs `mise run check` on Ubuntu and Windows on every push and pull request, with Git LFS skipped and placeholder WCL credentials; a `real-fixtures` job with the LFS fixtures runs only when started by hand | The real fixtures are about 200 MB against a 1 GB monthly LFS bandwidth allowance; tests that need them skip on pointer files. Config requires the WCL credentials, which locally come from `.env`, and unit tests never call WCL | 2026-10-17 |
| D14 | Friendly damage | `config/registries/friendly_damage.yaml` classes abilities on the player's side (class spells, trinkets, borrowed powers) that hurt players as `redistribution`, `delayed_boss`, `self_inflicted` (a cost the player chose) or `involuntary` (self-damage the player can't control, such as a trinket proc), matched by ability ID whatever the row's source. Each entry also carries an `origin` (`class`, `borrowed_power`, `item`, or null when unknown); items aren't called seasonal because players can keep them past their season. The file is grouped by class, then by origin within each class, and a `class`-origin entry names its player class in a trailing comment. Friendly damage (registry abilities, plus any other friendly-source damage the encounter file doesn't name, as `unclassified`) never counts toward the unregistered share. Its hash, `friendly_damage_hash`, joins `produced_from`, the findings `cache_key` and the `EncounterData` records key, next to `registry_hash`. Player self-damage leaves encounter files' `ignored_abilities` for the registry (Shadow Word: Death, the only one) | It was all of Ula'tek's unregistered share, which hid nothing about the boss but kept the share above zero. The registry changes digest numbers the analyst cites, so an edit must miss the findings cache like a cooldown-registry edit does. One file classifies all player damage; Shadow Word: Death's backlash is recorded with the Environment (-1) as source, so the registry can't rely on `source_friendly` alone | 2026-10-18 |
| D15 | SimC data package | SimC's generated client data lives in its own sub-package, `src/khadbot/simc/`: `codes.py` (effect, aura and target codes with SimC's names), `client_data.py` (the table parser and records) and `fetch.py` (pinned downloads). Anything that turns that data into encounter config stays in `encounter/` (`registry_candidates.py`), and `khadbot.simc` imports nothing from the modes | The data isn't specific to encounter review: the coach's planned SimC tool needs the same spells, talents and cooldowns, and reaching into `encounter/` from the coach would cross the mode boundary. More SimC work is planned, so the package starts with its three concerns already apart | 2026-10-18 |
| D16 | Cooldown registry source | Cooldown-registry candidates come from the game client's data tables as SimulationCraft generates them (`engine/dbc/generated/`, pinned to a SimC commit and stamped with the WoW build). `khadbot.simc` parses them (`client_data.py`, with `fetch.py` downloading a pinned commit) and `encounter/registry_candidates.py` proposes every player spell with a cooldown of at least 60 s whose effects, or the spells they trigger, are defensive (damage taken down, absorb, immunity, more health, split damage, more healing taken, cheat death), healing (an area heal or a heal for a share of maximum health) or support (any other aura cast on one ally). `kind` comes from the effect's target: caster only is `personal`, one ally `external`, an area or the raid `raid`. Casts that work through an area trigger, totem or scripted effect fall back to same-name spells for their aura. `scripts/build_cooldown_registry.py` writes a review file under `KHADBOT_DATA_DIR` and never edits `cooldowns.yaml`; a person copies accepted entries in with `verified: false`. Game client data now counts as a source for spell IDs, cooldowns and durations next to recorded fixtures and the user. The same script writes a second review file for `friendly_damage.yaml`: every spell with a nonzero class mask, player spell or not, with a direct damage or leech effect or a periodic damage or leech aura whose targets are only the caster or allies, with the class from the mask, `class: null` for a person to fill and a `registry_check` with the same statuses (`kind_differs` meaning the entry's origin is not `class`) | Client data covers every class and spec with exact cooldowns and durations, which guides don't. Wowhead's terms forbid scraping and its guides are partial prose. The Blizzard Game Data API lists talents but gives cooldowns only as tooltip text. SimC already ships the tables, parsed, for each build. Kinds and the defensive filter are a heuristic (scripted spells such as Survival Instincts and Healing Tide Totem can't be classified from data, so the review file lists them separately), hence nothing is verified automatically. For friendly damage the data can't tell shared-out, delayed and self-chosen damage apart, and it misses damage done in server scripts (Blessing of Sacrifice, Time Dilation), spells SimC leaves out (Spirit Link's damage, 98021) and items or borrowed powers (class mask 0), so recorded logs stay the main source and the review file is a cross-check for class abilities the logs haven't shown yet | 2026-10-18 |
| D17 | Cooldown aura IDs | A cooldown-registry entry may list `aura_spell_ids`: the spells its aura is applied under when they differ from the cast (Rallying Cry casts 97462 and buffs 97463; Anti-Magic Zone, Spirit Link Totem and Power Word: Barrier likewise). The Buffs filter (`fetch_filter_inputs`) fetches each entry's cast ID and aura IDs (`CooldownRegistry.aura_ids`), and `aura_intervals` reports an interval under an aura ID as its entry's `spell_id`, so window coverage and death-window `active_defensives` name the cooldown as before. An aura ID may belong to one entry only and may not be any entry's cast ID; the loader rejects a clash. Casts and `cd:` refs still use the cast ID. The values come from the review file of `scripts/build_cooldown_registry.py` and stay unverified | These four raid cooldowns were cast 13 to 21 times a night with no Buffs row, so no damage window or death ever counted them. Mapping back to the entry keeps the digest's ID space to cast IDs, which refs and the anomaly list already use. Adding the field changes `registry_hash` and the Buffs filter hash, so cached findings miss and the next ingest refetches Buffs for every pull; the recorded real fixtures keep the cast-only filter they were recorded with | 2026-10-18 |
| D17 | Late cooldowns and deaths | `WindowCoverage` gains `cooldowns_late`: each raid cooldown not active at the occurrence's first hit whose aura started after it, up to the occurrence's last hit or 5 s after the first hit (whichever is later), with `late_ms` from the first hit to its earliest start. `covered` still judges the first hit only. In death records, an aura removed at most 100 ms before the death row counts in `active_defensives` when the cooldown was still running: the same cooldown's aura was still up on another player at the death, or its registry `duration_s` had not run out | A cooldown used just after a window starts is the most useful thing to tell the raid (used earlier, it covers the first tick), and the first-hit check hid it. The game strips a dying player's auras 1-56 ms before WCL's death row (43 of 585 deaths on the recorded nights), so the exact-time check missed defensives that were up when the player died. Requiring the cooldown to still be running keeps out Rallying Cry expiring and taking its extra health with it, which kills players 43-104 ms before the row; registry durations are mostly unknown and talents lengthen them, so other players' auras are the main test | 2026-10-18 |
| D18 | Cooldown categories | Player cooldowns are split by category into one registry file each under `config/registries/`: `defensive_cooldowns.yaml` (was `cooldowns.yaml`; damage reduction, absorbs, immunities and healing cooldowns), with `offensive_cooldowns.yaml` and later `utility_cooldowns.yaml` beside it. Every file shares the `CooldownRegistry` schema and names its own `category` at the top, which `load_cooldowns(category)` / `parse_cooldowns(data, category)` check. An entry's `kind` is now `target` (`personal`, `external`, `raid`: who the effect lands on), so it can't be confused with the category. `registry_hash` becomes `cooldowns_hash()`, and its key in `produced_from`, the `EncounterData` records key and the findings `cache_key` is `defensive_cooldowns_hash`. `build_pull_records`, `build_digest`, `EncounterData`, `death_records` and `window_coverage` take the defensive file as `defensive`. The candidate generator is `scripts/build_registries.py` (`defensive_candidates()`, `--defensive`, `--out-dir`), and its registry check names the compared field (`target_differs`, `origin_differs`) | Offensive cooldowns must never count toward damage-window coverage or death-window defensives, and a separate file makes that structural rather than a filter every consumer must remember. "Cooldown registry" no longer said which cooldowns, and `kind` read like the category. Renaming the hash key misses every cached finding and records entry once and changes the `produced_from` of every digest golden; the Buffs filter and its hash are unchanged, so nothing is refetched | 2026-10-18 |
| D19 | Offensive cooldown registry | `config/registries/offensive_cooldowns.yaml` (`category: offensive`) holds cooldowns that raise damage done. Its content hash, `offensive_cooldowns_hash`, joins `produced_from`, the `EncounterData` records key and the findings `cache_key` next to `defensive_cooldowns_hash`. `check_cooldown_ids` rejects a cast or aura ID found in more than one cooldown file; `build_pull_records` runs it whenever it loads the files. `build_pull_records`, `build_digest` and `EncounterData` take the file as `offensive` | Offensive cooldowns are reported, not judged against damage windows or deaths, so they live apart from the defensive file. Their own hash means editing them misses cached findings, as any input the analyst may cite must. One owner per ID keeps every `cd:` ref and aura interval pointing at a single entry | 2026-10-18 |
| D20 | Offensive Buffs fetch | Offensive cooldowns get a second Buffs request with its own filter: `FetchSpec.part` (`"offensive"`) names it, and `FetchSpec.key` (`Buffs:offensive`) keeps its pages and truncation flag apart within a pull. Its pages share the `Buffs/` cache directory under their own filter hash, and normalize tags their rows `Buffs` like the defensive ones. `specs_for` adds the request only when the offensive file has IDs, since an omitted filter fetches every buff. `ingest_report` takes `defensive_spell_ids` and `offensive_spell_ids` (was `registry_spell_ids`), which `fetch_filter_inputs(defensive, offensive, encounters)` builds after checking the files share no ID | Editing one cooldown file refetches only its own Buffs pages, and the defensive filter, already 95 IDs against a WCL length limit nobody knows, doesn't grow. An empty offensive file leaves every pull's events, `events_hash` and cache unchanged | 2026-10-18 |
| D21 | Offensive cooldowns in the digest | `CooldownUse` gains `category` (`defensive` / `offensive`, from the file its spell is in), and `cooldown_uses` reads both files. `PullDigest.defensive_cooldowns` (was `cooldowns`) holds defensive casts only (cap 30) and the new `offensive_cooldowns` the offensive ones (`OFFENSIVE_COOLDOWN_CAP`, 15). The offensive casts kept are raid-wide ones first, then each spell's first cast, then each one's second and so on, earliest first, shown in time order. The rendered digest has DEFENSIVE COOLDOWNS (was COOLDOWNS) before DAMAGE WINDOWS and OFFENSIVE COOLDOWNS after it. `PullRecords.cooldowns` keeps every cast, so `cd:<spell_id>:<n>` refs resolve for both categories, and `cooldown_timeline` returns both with their category. An unverified offensive cooldown that was cast is a `cooldown_unverified` anomaly like a defensive one. Window coverage, `cooldowns_late` and death-window `active_defensives` still read only the defensive file | A raid casts many more offensive cooldowns than defensive ones; one shared cap would push the defensives the analyst needs out of the digest. The recorded 10-minute pulls cast 30 or more offensive cooldowns, and a 30-line section took three of them over the 3,000-token digest ceiling (up to 3,205); at 15 the largest is 2,898. Keeping casts by time alone cut a mid-pull Bloodlust, and ranking externals first filled the section with Power Infusion, so raid casts come first and then one cast per spell at a time. Keeping the `cd:` ref and the timeline tool shared avoids a second ref kind and tool for the same kind of record. The tool's docstring changed, so the prompt version and cached findings change with it | 2026-10-18 |
| D22 | Offensive cooldown candidates | The registry generator proposes offensive cooldowns too: a player spell with a cooldown of at least 60 s whose own or triggered effects apply a positive damage-done, haste, critical-strike, mastery, versatility or stat aura (`OFFENSIVE_AURAS`, codes from SimC's `data_enums.hh` added to `simc/codes.py`). The offensive check runs before the "any other aura on one ally" support rule, so Power Infusion is offensive and Innervate stays support. Defensive classification ignores offensive effects. On a spell that has an offensive effect, its support auras and any defensive aura under 2 s from a spell it triggers are side effects, not a defensive cooldown; Bestial Wrath (19574) and Avatar (107574) are left out of the defensive candidates by name (`_NOT_DEFENSIVE`). A spell with both kinds of effect otherwise (Sentinel) is a candidate for both files. `scripts/build_registries.py` writes `offensive_cooldowns.candidates.yaml` beside the defensive one, with `in_other_registry` on candidates the other file already holds; the `scripted` list leaves out candidates of either kind | The same client data and target logic serve both files. On build 12.1.5.70448 the defensive heuristic misread three offensive cooldowns. Havoc's Metamorphosis (191427) triggers its haste buff (162264) and a 1 s landing immunity (201453); the 2 s rule drops the immunity, and limiting it to spells with an offensive effect keeps Cloak of Shadows, whose only defensive aura in the data is its 1 s triggered immunity (35729). Power Infusion's second aura (aura type 108) made it a support candidate until support auras on offensive spells were dropped. Bestial Wrath's pet aura (186254) carries the damage bonus and an absorb in one spell, and a talent puts AoE avoidance and damage reduction on Avatar's cast, so no structural rule sets them apart; they are named instead. With these rules the defensive candidates are the 61 defensive and healing spells already in the registry, plus support spells. A person settles the both-files cases, since the loader rejects a spell in both | 2026-10-18 |
| D23 | Defensive names in encounter mode | Anything in encounter mode that holds or counts only defensive cooldowns says so in its name: `PullDigest.defensive_cooldowns` (was `cooldowns`) and `DEFENSIVE_COOLDOWN_CAP`, `WindowCoverage.defensive_cooldowns_active` and `defensive_cooldowns_late` (were `cooldowns_active` and `cooldowns_late`), the encounter-file damage-window key `expected_defensive_cooldowns` (was `expected_cooldowns`) and the generator's `classify_defensive` (was `classify`). Names that cover every category stay generic: `PullRecords.cooldowns`, `CooldownUse`, the `cd:` ref, the `cooldown_unverified` anomaly, the `cooldown_timeline` tool and the registry files' `cooldowns:` list. `DeathRecord.active_defensives` already said so | Once offensive cooldowns joined the digest, a bare `cooldowns` next to `offensive_cooldowns` read as every cooldown, and a damage window's `expected_cooldowns` could be read as counting Bloodlust. Encounter files reject unknown keys, so a file still using `expected_cooldowns` fails at load instead of falling back to the default. The key changes `encfile_hash`, so annotations and findings rebuild once; the field renames change the analyst's tool output and the `cooldown_timeline` and `get_digest` docstrings, so the prompt version changes too | 2026-10-18 |
| D24 | Offensive spell modifiers and logged cast IDs | The offensive heuristic also counts spell-modifier auras (`A_ADD_PCT_MODIFIER`, `A_ADD_PCT_LABEL_MODIFIER` with a positive value on `P_GENERIC`, an effect's value `P_EFFECT_1` to `P_EFFECT_5`, `P_TICK_DAMAGE` or `P_CRIT_BONUS`; `A_ADD_FLAT_MODIFIER`, `A_ADD_FLAT_LABEL_MODIFIER` with a positive `P_CRIT`) and positive auto-attack speed and damage auras. `Effect` gains `misc_value`, the property a modifier changes. The minimum cooldown stays 60 s; 45 s cooldowns the logs show as major (Pillar of Frost, Dark Transformation) and scripted ones (Breath of Sindragosa, Convoke the Spirits, Zenith, the warlock summons) are entered by hand from their own spell data. When recorded logs show a cooldown cast under another ID than its player spell, the registry uses the logged ID: Havoc's Metamorphosis is 200166, not 191427 | On build 12.1.5.70448 the old rule found 18 offensive candidates and missed most damage cooldowns, which raise damage through spell modifiers rather than a damage-done aura (Recklessness, Combustion, Shadow Blades, Berserk, the Incarnations, Invoke Xuen). With modifiers it finds 44 and the defensive candidates don't change. The data doesn't say whether a modifier's spells deal damage or heal, so healer cooldowns (Nature's Swiftness, Invoke Yu'lon) show up and a person leaves them out. Lowering the minimum to 45 s added only Pillar of Frost and a rotational spell to the offensive list but three short defensives to the other. Cooldown uses are matched on cast ID, and over 62 recorded pulls 191427 was never cast while 200166 was cast 131 times, so the old entry counted no Metamorphosis | 2026-10-18 |
| D25 | Utility cooldown candidates | The registry generator writes a third review file, `utility_cooldowns.candidates.yaml`, for a utility file that doesn't exist yet. A utility candidate is a player spell whose own, triggered or same-name effects help allies (role `movement`: a positive speed aura; `mana`: a mana energize, mana regeneration or a lower mana cost; `resurrect`: a resurrect effect on a corpse; `freedom`: immunity to roots, snares or slows; `reposition`: a jump or pull of an ally) or control enemies (`stun`, `incapacitate`, `root`, `slow`, `grip`, `knockback`, by aura type, effect type or, for dummy effects, the effect's mechanic). Enemy-side `target` is `enemy` (one) or `enemies` (an area); the ally-side values stay `personal`, `external`, `raid`. When a spell has both, the enemy side wins the target and `role` is the strongest kind on that side (`UTILITY_KINDS` order). Script effects count as an indirect link for utility only, so Leap of Faith and Shockwave find their same-name spells. The utility file has its own floor, `--utility-min-cooldown` (30 s); the other files keep 60 s. `simc.client_data.Effect` gains `misc_value` and `mechanic`. Each candidate names the other candidate lists it is in (`also_candidate`) and any effect indexes SimC left out (`dropped_effects`). A separate `dropped_effects` section lists long-cooldown spells in no list that lost effects, and the `scripted` list in every review file now leaves out utility candidates (78 to 63 spells at 60 s) | Most stuns, knockbacks and roots are on 30-60 s cooldowns, so a 60 s floor would miss Hammer of Justice, Shockwave and Ring of Peace. SimC keeps only the effects its simulations read: Rebirth and Intercession lost their resurrection, Blind its disorient and Intimidating Shout its fear, and Mana Tide Totem, Symbol of Hope and Wind Rush Totem's speed aren't in the tables at all. The heuristic can't recover those, so the review file names the gaps instead of guessing. Nothing loads a utility file yet; the registry schema, `CooldownCategory` and the Buffs fetch take the `enemy`/`enemies` targets when one exists | 2026-10-18 |
| D26 | Utility cooldown registry | `config/registries/utility_cooldowns.yaml` (`category: utility`) holds 58 utility cooldowns taken from the generator's review file. `CooldownCategory` gains `utility`, `CooldownTarget` gains `enemy` and `enemies`, and `CooldownEntry` gains `role` (`UtilityRole`, the generator's kinds). The registry validator requires a role on every utility entry and rejects a role or an enemy target in the defensive and offensive files. An unset role is left out of the model dump, so the defensive and offensive content hashes are unchanged. `load_cooldowns("utility")` reads the file; `check_cooldown_ids` covers all three files in tests, and the generator's `--utility` path adds a utility registry check and `in_other_registry` flags. Left out: the 7 candidates already in another file (Innervate stays defensive) and 14 damage cooldowns whose utility is a side effect (The Hunt, Frozen Orb, Avenging Crusader and others). Blind, Intimidating Shout (incapacitate) and Gorefiend's Grasp (grip) have roles set by hand. Intimidation's shared stun aura sits on the Beast Mastery and Survival cast only. Nothing in encounter mode reads the file yet: ingest, digest, `fetch_filter_inputs` and the findings cache key still take only the defensive and offensive files | Enemy targets and a role are what separate a stun from a raid speed boost, and keeping them out of the other files keeps damage-window coverage and death-window defensives as they were. Keeping the hashes means no cached finding or digest golden changes until the utility file is wired in. A spell in one file only is the rule from D19; the side-effect damage cooldowns would block those spells from the offensive file later | 2026-10-18 |
| D27 | Innervate is a utility cooldown | Innervate (29166) moves from `defensive_cooldowns.yaml` to `utility_cooldowns.yaml` (`target: external`, `role: mana`), leaving 62 defensive and 59 utility entries | Innervate restores mana and keeps no one alive, so as a defensive it could show among a death's `active_defensives`. The move changes `defensive_cooldowns_hash` and the defensive Buffs filter, as Power Infusion's move did: cached findings and annotations rebuild once, and each pull's defensive Buffs pages are refetched | 2026-10-18 |
| D28 | Utility cooldowns in encounter mode | Encounter mode reads `utility_cooldowns.yaml`. `cooldown_uses` takes all three files, so `CooldownUse.category` can be `utility` and `cd:` refs and `cooldown_timeline` cover utility casts. `PullDigest.utility_cooldowns` holds at most `UTILITY_COOLDOWN_CAP` (1) of them, rendered as UTILITY COOLDOWNS after OFFENSIVE COOLDOWNS: raid-wide casts first, then casts on enemies or another player, then the caster's own, and within a tier each spell's first cast, then its second, earliest first (`capped_utility`, sharing `_capped` with `capped_offensive`). `utility_cooldowns_hash` joins `produced_from`, the `EncounterData` records key and the findings `cache_key`; `build_pull_records`, `build_digest` and `EncounterData` take the file as `utility`. No request is added: utility casts come from the unfiltered friendly Casts request, and `fetch_filter_inputs(..., utility=)` only checks the file against the others. Utility casts raise no `cooldown_unverified` anomaly | Nothing reads a utility aura yet: coverage and death windows stay defensive-only, so a Buffs filter for ally auras or a Debuffs request on enemies (stuns, roots) would spend WCL points for no reader, and no fixture has recorded the Enemies Debuffs cost. The recorded pulls cast 2 to 71 utility cooldowns (before Innervate moved), 107 of 246 of them Death's Advance and 45 Divine Steed, so personal movement goes last. With the 40-entry offensive file the largest recorded digest is about 2,975 tokens without a utility section and 3,062 with four utility lines, each about 21 tokens; even one line reaches 3,001. The digest ceiling rises from 3,000 to 3,100 tokens and the cap is 1, so the section names the top-ranked raid or enemy-side cast and `cooldown_timeline` lists the rest. Unverified-anomaly codes for utility casts would have added about 90 tokens to the largest digests for entries whose cooldown and duration nothing reads | 2026-10-18 |
| D29 | Postgres foundation | Hosted mode keeps its relational state in PostgreSQL, major version 17 (the CI `hosted` job runs the `postgres:17` image), through `psycopg` 3 with `psycopg_pool` and no ORM. `DatabaseConfig` reads `KHADBOT_MODE` (`local`, the default, or `hosted`), `DATABASE_URL` and the pool sizes. `python main.py migrate` (`mise run db:migrate`) applies the numbered files in `src/khadbot/db/migrations/` in order: each file and its `schema_migrations` row (version, name, checksum) commit together, a session advisory lock serialises runners, and an applied file whose content (line endings ignored) has changed is an error, so a schema change is always a new file. `FindingsStore` is now a protocol; `SqliteFindingsStore` is the old class and `PostgresFindingsStore` stores the report as `jsonb` with the same methods. `default_findings_store()` returns the Postgres store when `KHADBOT_MODE=hosted`, else the SQLite file. Local mode never imports the connection code and needs no database. Tests marked `hosted` live in `tests/integration/hosted/`, skip without `DATABASE_URL`, and give each test its own schema | Local mode must keep working with nothing installed, so the choice is configuration, not code: one protocol, two implementations, and one contract test suite both pass. Plain SQL files and a 100-line runner are enough for a schema this size, and a checksum catches the edited-after-applied file that would otherwise leave databases diverged. Version 17 is the newest major the common managed hosts all offer; the pin is in one place (the workflow) and moves with a new migration check | 2026-10-20 |
| D30 | Code versions in cache keys | `ANNOTATION_VERSION`, `RECORDS_VERSION` (new, `digest.py`, bumped by hand when analysis or record-building code changes its output) the cutoff fraction and `pull_hash` (a hash of the whole stored `Pull`, so any `--refresh-meta` change misses) join `PullDigest.produced_from`, the `EncounterData` records key and the findings `cache_key`. The annotation cache sidecar also stores `pull_hash`, a narrower hash of the `Pull` fields annotation reads (`start_ms`, `end_ms`, `duration_ms`, `kill`, `phase_transitions`, `friendly_player_ids`); a sidecar without it misses once | Findings are about to be shared between users, so a result built by old code must not be served after a change. Code can't be hashed reliably, so a hand-bumped constant is the cheapest honest signal; the cutoff and pull metadata are plain data and are hashed. The change misses every cached annotation and finding once and changes `produced_from` in every digest golden | 2026-10-20 |
| D31 | Blob storage seam | The raw page cache, the event store and the annotation cache read and write whole objects through a `BlobStore` protocol (`src/khadbot/storage/`: `get`, atomic `put`, `exists`, recursive sorted `list` by string prefix, `delete`) under keys equal to their old paths relative to `KHADBOT_DATA_DIR` (`raw/…`, `events/…`, `annotated/…`). `storage.default_blob_store()` is the one factory; it returns `LocalBlobStore` over the data directory, and the `default_*` factories, `EncounterData.from_config` and `create_app` use it. Each persistence class takes a store and a key prefix, and still accepts a directory path. Keys are validated (relative, no `.`/`..` or empty segments, no characters a Windows path can't hold, no `.tmp` suffix). The findings and checkpoint databases are not blobs. See "Blob storage seam (2026-10-20)" under implementation notes | A hosted deployment needs object storage without rewriting every stage, and mirroring the paths means an existing data directory works unchanged: the local store writes the same bytes the path-based classes did, which a golden manifest built by the old classes checks. Whole objects suit both parquet-per-pull and S3, and timings on the largest recorded night stayed within run-to-run noise | 2026-10-20 |
| D32 | User token through ingest | `ingest_report` and `fetch_pull` take an optional `token_provider` (an async callable returning a user's WCL access token). Every ingest request, including the report metadata and the `rateLimitData` reads, passes the token to `WarcraftLogsClient.query(user_token=…)`, which routes to `/api/v2/user`; with no provider ingest uses the app token exactly as before. The provider is called per request so it can refresh; on a 401 (a new `WarcraftLogsUnauthorizedError` from the client) it is called once more and the request retried once, and a second 401 raises `UserTokenRejectedError`. The cached `report.json` gains a `khadbotFetchedWith` key, `"user"` or `"app"`, written only when ingest fetches the metadata; a file without it reads as unrecorded. It never names the user | The hosted service fetches on a signed-in user's own budget and must later decide who may read a cached report; a report fetched with a user token can be private, and an app-token fetch proves it is not. Recording the kind, not the user, keeps identity out of the blob store. A provider instead of a token string lets the token refresh mid-ingest (a night's fetch can outlast an access token) without ingest knowing about refresh tokens. Local mode passes no provider and is unchanged | 2026-10-20 |
| D33 | Persisted pull records | `PullRecords` persist as one JSON blob per pull at `records/<report>/<fight>__<hash>.json` in the blob store (`encounter/records_store.py`, `RecordsStore`). `<hash>` is the first 16 hex of SHA-256 over the digest's `produced_from`, now built by `digest.records_inputs`, which both `build_pull_records` and the `EncounterData` in-process key use, so the three can't disagree about what invalidates. The blob (`StoredRecords`) holds deaths, mechanics, cooldown uses, window coverage and the capped digest; the annotated frame is read through the annotation cache, the pull, actors and abilities from the event store and the encounter file from `config/`. `EncounterData` reads its LRU, then the records store, then builds and writes; a failed write is logged and does not fail the read, and an unparsable blob or one whose stored `produced_from` differs from the inputs is a miss that the rebuild overwrites. `EncounterData.from_config` and `create_app` pass a `RecordsStore` over their blob store; an `EncounterData` without one behaves as before. No version constant changed: `RECORDS_VERSION` stays 1 because what records hold is unchanged | A hosted worker or API process must serve analysis another process built, and the keys have to follow the same hashes that already say when a result is stale. Leaving the frame out keeps a blob at about 79 KB a pull (1.66 MB for a 21-pull night, against 23 MB of annotated frames) and reuses a cache that already exists. On a real 21-pull night a stored load took 3.9 s against 10.5 s to build, and parsing a blob takes about 1.3 ms: what remains is YAML parsing of the authored files, which every lookup repeats, and reading the frame. Old blobs are never deleted, since a changed input only adds a key; cleaning them is retention work | 2026-10-20 |
| D34 | S3-compatible blob store | `KHADBOT_STORAGE=s3` makes `storage.default_blob_store()` return an `S3BlobStore` (`storage/s3.py`, `boto3`) over the bucket named by `KHADBOT_S3_BUCKET`, `KHADBOT_S3_PREFIX`, `KHADBOT_S3_ENDPOINT` and `KHADBOT_S3_REGION` (`StorageConfig`), wrapped in a `CompressedBlobStore` (`storage/compressed.py`) for `raw/` only; the default, `local`, is unchanged and never compresses, so existing data directories are untouched. Credentials are boto3's own (`AWS_*` variables, a profile or a role), not KhadBot settings. A custom endpoint uses path-style addressing, which MinIO and R2 both accept. `put` is a single `put_object` (S3 replaces objects atomically); `list` pages through `list_objects_v2` and treats the prefix as a string prefix, like the local store; only a missing-object error reads as missing, so a bad bucket or credentials raise. Raw pages are stored as `<key>.zst` (zstd level 9, `zstandard`, a fresh compressor per call for thread safety; level 9 stores 27% less than the default level 3 for about four times the CPU, which is still far above WCL's page rate); callers keep the plain key, listings show plain keys, and an object under `raw/` without the suffix is not seen. Parquet and the small JSON objects are stored as they are. The ratio and times measured on a recorded night are in `docs/wcl-api.md`. The contract suite runs against S3 (moto, in memory) and the wrapper in the unit tests and against a real service in `tests/integration/hosted/test_blob_s3.py`, which skips without `KHADBOT_S3_BUCKET`. |

## Implementation notes (deviations from the plan text)

- **Raw cache directory for enemy data**: `<DataType>-Enemies` is used for `hostility="Enemies"` so friendly and
  enemy `Casts` do not collide when both have `filter=none` (plan 5.2 only names `<DataType>`).
- **`build_filter` inputs (M1.3)**: takes plain ID lists (`registry_spell_ids`, `encounter_ability_ids`) instead of
  `CooldownRegistry` / `EncounterFile`, because those models arrive in M2.1. M2 passes IDs derived from them.
  `ingest_report` takes the same two optional lists.
- **`EventStore` additions (M1.7)**: `write_pull` takes an optional `truncated` list and the protocol gains
  `truncated(pull_key)`, so truncation flags live in `pulls.json` next to `events_hash` (plan M1.8).
- **Fetch keys**: `fetch_pull` returns `{dir_name: truncated}` (not `data_type`) so friendly/enemy `Casts` are distinct.
  "Truncated" means pagination stopped because `nextPageTimestamp` did not advance.
- **CLI ingest filters (M2.1)**: `/ingest` and `main.py ingest` now pass `registry_spell_ids` from
  `config/registries/cooldowns.yaml` and `enemy_cast_ids` from every encounter file under `config/encounters/`
  (`EncounterFile.ability_ids()`: mechanics, damage windows, phase-trigger abilities). A report whose boss has no
  encounter file still fetches enemy `Casts` unfiltered.
- **Cooldown registry kinds (M2.1)**: the 17 ported entries carry a first-pass `kind` (raid / external / personal);
  H4 verifies kinds along with durations. `class` is exposed as `CooldownEntry.class_name`.
- **Unknown keys in authored files are errors** (`extra="forbid"`), so typos fail fast.
- **mypy**: clean on `main` since fdaed26, which also added `types-pyyaml` to dev deps; the encounter work adds no errors.
- **Package casing (M0.1)** was committed on its own, as the plan requires, before the rest of M0.
- **Ingest package (2026-10-11)**: `wcl_queries`, `fetch`, `normalize` and the ingest orchestration (now
  `pipeline.py`) live in `encounter/ingest/`, under the module layout rule in CLAUDE.md. App code imports
  `ingest_report` and `default_store` from the package. `EVENT_SCHEMA` and `empty_events` moved from `normalize`
  to `store`, which owns the schema it persists, so the shared store never imports ingest internals.
- **Analyst package (2026-10-11)**: `analyst.py` (now `graph.py`), `query_tools` and `versioning` live in
  `encounter/analyst/`. App code imports `run_analysis`, `review` and `estimate_review_cost` from the package;
  tests that patch module attributes import `analyst.graph` itself. Tool names, docstrings and argument schemas
  are unchanged, so the prompt version and the findings cache key are too.
- **Analyst modules (2026-10-12)**: `graph.py` was split by concern: `prompts.py` (prompt templates and
  fixed replies, so a prompt edit, which changes the prompt version, is one file in a diff), `verify.py`
  (`check_finding`, `verify_report`; the conversation agent's answer check will reuse them),
  `graph.py` (the single-question analyst and `run_analysis`) and `review_graph.py` (per-pull fan-out,
  findings cache, synthesis, `review` and the cost estimate). The review module is not called
  `review.py` because the package exports a `review` function, which would shadow it. The graph's
  `make_run`, `analysis_config` and `invoke` are public because the review graph uses them. Code and
  prompts moved unchanged: all three prompt versions match the previous layout and `GRAPH_VERSION` stays.
- **Annotate package (2026-10-12)**: `phases.py` and `annotate.py` form `encounter/annotate/`: `frame.py`
  (`Annotated`, the column schema, `is_hit`, `type_is`, `aggro_events`, `ANNOTATION_VERSION`), `failures.py`
  (the failure rules and blame, where the planned stack-count, hit-while-debuffed and add-cause rules go),
  `pipeline.py` (`annotate()`, clusters, wipe cutoff), `cache.py` (`AnnotationCache`, `annotate_pull`) and
  `phases.py`. `frame.py` exists so the failure rules and `annotate()` don't import each other. The core
  module is not `annotate.py` because the package exports an `annotate` function. App code imports from the
  package as before. Code moved unchanged (`_failures` is now the public `mechanic_failures`), so annotated
  frames are identical and `ANNOTATION_VERSION` stays 3.
- **Encounter fixtures (2026-10-12)**: `wcl/`, `wcl_views/`, `encounters/` and `registries/` live under
  `tests/fixtures/encounter/`, next to the coach payload modules in `tests/fixtures/`. `FIXTURES` in
  `tests/helpers/encounter/synth.py` names that encounter root. The Git LFS rules move with `wcl/`, and the
  pre-commit exclude follows the new path.

### M2 (encounter file, annotation, digest)

- **SYNTH01 generator fix**: the sim applied damage and heals in script order, so `hitPoints` and deaths did not
  follow the timeline. It now applies them chronologically; deaths match the fixture README (fight 1: 8, 23, 41,
  50 s; fight 2: 55 s). Normalized goldens were regenerated in the same commit.
- **Ability names**: `EventStore` gains `write_abilities` / `abilities(code)` (`events/<report>/abilities.json`,
  from `masterData.abilities`) so death records and unregistered damage can name abilities. Reports ingested before
  M2 have no names until re-ingested.
- **Signatures vs plan section 10**: `annotate(pull, events, enc, actors, *, cutoff_fraction)` (no registry; nothing
  in annotation uses it). `Annotated` carries the `pull`, so analysis functions take no separate `pull`.
  `death_records(a, actors, registry=None, abilities=None)` needs the registry for `active_defensives`.
  `detect_phases` keeps its signature; `unmatched_phase(spans, enc)` names the trigger that stopped detection.
  `build_digest` adds keyword-only `cache`, `cutoff_fraction` and `unregistered_cap`.
- **Window coverage counts `kind: raid` cooldowns only** (plan 6.3 said raid/external). A damage window is raid-wide
  damage; an external on one player does not cover it. Plan section 6.3 updated to match.
- **Hits and clusters**: a "hit" is a `damage` event on a friendly target. Clusters, `window_occurrence`,
  `MechanicStat.hits/damage_taken` and the death window all use hits. Damage figures use WCL `amount` (after
  absorbs and overkill). `not_interrupted` failures are the enemy cast rows, so they have no player in
  `failures_by_player`; `soak_count_lt` failures mark a cluster's first row.
- **`time_to_die_ms`** is None when the actor had no reading at >= 90% HP in that life (not only when HP data is
  missing). A second death's window and time-to-die are clipped to after the previous death (battle res).
- **Anomalies**: `phase_trigger_unmatched:<id>` is raised only when WCL's phase transitions show the pull reached
  that phase (rule since 2026-10-16 below); an early wipe never reaching P2 is not an anomaly.
  `missing_data_type:<T>` checks only DamageTaken, Casts and Healing, which every real pull has; zero Deaths,
  Interrupts or Dispels rows are legitimate. `unregistered_damage:<pct>` is raised only when an encounter file
  exists and the share is >= 10%.
- **Triggers needing data not fetched yet**: `boss_hp_below` needs damage on the boss (no DamageDone spec) and
  `boss_aura_applied` needs auras on the boss (Buffs are fetched for friendlies only). They match nothing on real
  data until S1 adds those specs; prefer `boss_cast`, `wcl_phase` or `time` in H3 until then.
- **CLI**: `/digest [<report-code>] <fight> [--json] [--unregistered]` and `/pulls [<report-code>]` default to the
  last report ingested in the session. `--unregistered` lists up to 20 abilities. Golden files can be rewritten
  with `pytest --update-goldens` (`UPDATE_GOLDEN=1` still works).
- **Offline ingest of SYNTH01 through the CLI** misses the cache, because the CLI derives Buffs / enemy-Casts filters
  from `config/` while the fixture was recorded with synthetic IDs. Tests pass the synthetic IDs explicitly.
- **Encounter file layout and difficulties (2026-10-11)**: files live at
  `config/encounters/<zone_id>-<zone_slug>/<encounter_id>-<encounter_slug>.yaml` (Ula'tek:
  `53-the_venomous_abyss/3492-ulatek.yaml`), so a file says which fight it is without opening it. The zone ID is
  the WCL zone ID from report metadata, the only instance ID the data carries. Slugs are lowercase with apostrophes
  dropped and other runs of non-alphanumerics turned into `_`; a hyphen separates ID from slug because a second dot
  reads as a file extension. The file declares `zone: {id, name}`, and the loader rejects a file whose path doesn't
  match its declared zone, encounter ID and name, and an encounter ID with two files. Every entry (phase, mechanic,
  damage window, ignored ability) has a required `difficulties` list (`lfr`, `normal`, `heroic`, `mythic`; WCL
  codes 1, 3, 4, 5, of which only 4 and 5 are confirmed on recorded reports). `ignore_ability_ids` became
  `ignored_abilities: [{ability_id, name, difficulties}]`. Validation runs per difficulty, so an ID or ability may
  repeat on entries whose difficulties don't overlap (for example a different failure rule on Mythic). Analysis
  uses `load_encounter_view(encounter_id, pull.difficulty)`: only that difficulty's entries, and None (coverage
  `none`) when no phase covers the pull's difficulty or the difficulty is unknown. `encfile_hash` is taken over that
  view, so editing one difficulty's entries leaves the other difficulties' annotations and findings cached. The
  ingest filter still uses every difficulty's ability IDs. Plan section 6.2 updated to match (plan 0.3.4).
- **Failure blame (2026-10-11)**: annotation adds a `blamed_id` column, the player charged with each failure
  row. The existing rules charge the friendly player hit (or the debuffed player for `debuff_duration_gt`);
  `not_interrupted` and `soak_count_lt` charge no one. `MechanicStat.failures_by_player` and
  `MechanicCluster.failed_players` now count `blamed_id`, not the failure row's target, and an `event:` ref on a
  failure row adds `blamed` (a name). Four rules are added so mechanics that punish the raid for one player's
  mistake can be charged to that player:
  - `per_cluster` + `blame`: one failure per cluster, on its first hit. `blame: {by: cast_target, ability_id, ms}`
    charges the friendly target of the latest enemy cast of `ability_id` in the `ms` before;
    `{by: aggro_holder, npc_game_id, ms}` charges the NPC's aggro holder (matched by game ID, since a boss can have
    two actors with one name): the player behind its latest melee swing or taunt in the Threat data in the `ms`
    before (see the Threat entries below). With no match the failure still counts, charged to no one. `blame` is required by `per_cluster` and rejected on every other rule.
  - `hit_amount_gt {value}`: each hit larger than `value` before mitigation (`unmitigated`, else `amount +
    absorbed`), for mechanics that hit everyone but hit hardest those standing in the wrong place.
  - `aura_applied`: each application of one of the mechanic's auras on a player, charged to that player. Like
    `not_interrupted` rows, these are not hits and belong to no cluster.
  - `hits_on_non_tank {ms}`: one failure per player per cluster for players other than the aggro holder of the
    NPC instance that hit them (latest swing or taunt on it in the `ms` before their first hit). Instances matter
    because adds of one kind can share an actor ID (both Weakened Doomscales are one actor), and only the first hit
    is judged because NPCs stop swinging while they cast, so DoT ticks on the tank would otherwise read as failures.
  Annotated frames carry `ANNOTATION_VERSION` (now 3) in their cache sidecar, so frames from before the change
  are rebuilt. The optional `blame` field also changes every encounter file's hash once. Plan sections 6.2
  (rules table) and 6.3 (`failures_by_player`) updated to match (plan 0.3.4).
- **Threat data and NPC instances (2026-10-11)**: ingest fetches a tenth data type, `Threat` (Friendlies side,
  about 1 point per pull; see `docs/wcl-api.md`). WCL Threat has no threat values: it is target tracking, NPC melee
  swings (misses included), taunts and NPC deaths. Normalize keeps the NPC swings, the taunt debuffs on NPCs and
  the NPC deaths, and drops the players' taunt casts, which repeat the Casts data. The event schema gains `source_instance` and
  `target_instance` (WCL `sourceInstance` / `targetInstance`), which number NPCs sharing one actor ID; every
  `events_hash` changes, so all pulls re-ingest from the raw cache. The digest gains `aggro`: per boss NPC, its
  melee swings, the top 3 targets with counts and shares, and the taunts that landed by player. It renders as an
  `AGGRO` section under the pull ref, and the `pull` ref resolves with the same `aggro` list so verify can check
  its numbers. Event rows and death top hits name NPCs with their instance (`Blightscale Rawling #51`).
  Recorded fixtures gained Threat pages and the encounter-filtered enemy Casts. Plan sections 6.1 (FetchSpec,
  event schema), 6.4 (digest) and 6.5 (`pull` ref) updated to match (plan 0.3.4). Blame reads it through
  `aggro_events` (see the blame entries).
- **Aggro bands (2026-10-11)**: `analysis/aggro.py` builds per-NPC-instance bands of who each NPC was attacking:
  a band starts at a swing or taunt on a new player and ends at the next switch, the NPC's death, 20 s with no
  swing or taunt (`AGGRO_QUIET_MS`, ending at the last event), or the pull end. That rule reproduces WCL's own
  threat table: on three pulls from three nights, 140 of 144 bands match to the millisecond, nothing ours calls
  aggro is missing from WCL's, and boss aggro never names a different player. The four differences are WCL
  holding a quiet band longer (see `docs/wcl-api.md`). Two pulls are unit tests against saved WCL tables.
  `aggro_holder(bands, npc_ids, t, instance)` answers who held an NPC at a time. Not used by the digest or
  blame yet.
- **Aggro for blame, reconciled with the Threat data (2026-10-11)**: compared over the 47
  Ula'tek pulls, per raid punishment:
  - The quiet-gap bands are the wrong question for punishments caused by nobody being in melee. The NPC
    stops swinging, so its band has often closed: 8 of 13 Mother's Wraths and 6 of 10 Rattler Slams fall
    outside any band. Blame wants the **last aggro holder**: the latest swing or taunt on that NPC within a
    lookback, with no quiet rule.
  - Mother's Wrath (raid): keep `cast_target`. The cast names the boss's actual target; in both cases where a
    taunt came first, it landed during the 5 s cast and the cast still named the original target.
  - Unchecked Rage: switch `melee_target` (damage hits only) to the last swing or taunt within 30 s. It
    charges `trMgApZCVYkv3jdq:18` at 393.5 s to Shinrael (Growl 2.8 s before) instead of Mekerakesh, and
    `trMgApZCVYkv3jdq:23` at 381.3 s, now charged to no one, to Mekerakesh (taunt 8.8 s before); 7 agree, and
    2 at the start of Stage Three have no holder either way.
  - Rattler Slam: same switch; identical results on these pulls (7 agree, 3 slams before anyone picked up
    the tail have no holder either way). Those unowned cases need the raid plan's tank assignments.
  - Desperate Thrash: judge each cone against the aggro holder of the casting add's instance
    (`source_instance`) instead of any recent melee target of the shared actor. Agrees on 93 of 102 first
    hits; it adds 4 hits on the other add's tank that today's rule misses and drops 1 false failure.
  - Positions would add "was the holder in melee range", which separates a tank out of position from other
    causes; attribution does not need it.
  Implemented: `blame: {by: aggro_holder, npc_game_id, ms}` (the latest swing or taunt on that NPC in the `ms`
  before, from `annotate.aggro_events`; none after the NPC's death) replaces `melee_target`, and
  `hits_on_non_tank` judges each player's first hit per cluster against the aggro holder of the casting NPC
  instance (a hit while the NPC has no holder is a failure). `ANNOTATION_VERSION` is 3. Over the 47 pulls the
  results match the comparison above: Unchecked Rage `trMgApZCVYkv3jdq:18` goes to Shinrael and
  `trMgApZCVYkv3jdq:23` at 381.3 s to Mekerakesh (2 rages now charged to no one, was 3); Mother's Wrath and
  Rattler Slam are unchanged; Desperate Thrash has 28 failures (was 24), the 4 new ones cone hits on the other
  add's tank. That leaves three kinds of failure attribution, each resting on one kind of evidence and testable with a few
  synthetic rows:
  1. **The player hit** (`any_hit`, `hits_per_cluster_gt`, `hit_amount_gt`, `aura_applied`,
     `debuff_duration_gt`): damage or aura rows on the failing player.
  2. **No one** (`not_interrupted`, `soak_count_lt`): an enemy cast or a whole cluster, with no single player
     at fault until raid-plan assignments exist.
  3. **The player responsible for an NPC** (`per_cluster` blame, `hits_on_non_tank`), from the Threat data
     only: `cast_target` (the NPC's own cast names its target) or `aggro_holder` (the NPC's latest swing or
     taunt), and for `hits_on_non_tank` the aggro holder of the casting NPC instance. `melee_target`, which
     read damage hits and missed taunts, is gone.
- **Positions: keep them, as a per-pull table (2026-10-16)**. Replaces "not kept yet" (2026-10-11). The
  measurements are in `docs/wcl-api.md`, section "Position data". On the 47 Heroic Ula'tek pulls:
  - **Exact where it matters.** A damage row carries the hit player's position, so a raid-wide hit gives
    every player's position at that moment. Otherwise a player's latest sample is 0.1 s old at the median and
    0.5 s at p95, the same for tanks, melee and ranged. NPCs are sampled only on their own swings and casts,
    but they stand on fixed spots and their cast rows carry their position.
  - **Scale: 1 yard = 100 units**, from the run-speed peak (700 to 750 units/s against 7 yards/s). Measured
    radii fit it: Volatile Purge about 5 yards, the Serpent's Bite soak about 7, Spectral Coils about 30.
  - **Kept for:**
    - Aggro-holder blame (Unchecked Rage, Rattler Slam). The holder's distance and whether they were alive
      separate "out of position" from "died" and "nobody had aggro". Every one of the 15 punishments with a
      living holder had them beyond the NPC's swing range, and every holder in range had died.
    - Who stood in a soak. Serpent's Bite: 291 of 300 Ingested Venom recipients stood within 7 yards of a
      bitten player, against 2 of 45 non-recipients, which confirms the debuff marks soakers. Spectral Coils:
      2,292 of 2,293 Soul Constrictor recipients stood within 30 yards of their coil, so that debuff marks the
      players at the impact. The debuff rules (`aura_applied`, the planned stack and aura rules) remain the
      way to charge these; positions back them up and show who stood where.
    - Mephitic Thrash, once a human confirms the radius: positions are the only signal there.
  - **Not needed for:** Circling Prey and Volatile Purge. Their damage rules already find the same players,
    though Volatile Purge positions also show whose eruption a player stood in.
  - **Form:** a separate table per pull next to the events file. Columns: `t_ms`, `actor_id`, `instance`, `x`,
    `y`, one row per sample. That is +30% storage (+11.5 MB on 38.7 MB) against +39% for columns on every
    event row, and it keeps the events schema and `events_hash` as they are. `facing` is left out until a
    cone rule needs it. A hit's own position is the target's sample at the hit's `t_ms`.
  - **Query helper:** "position of actor A at t" as an as-of lookup that returns the sample's age, and "distance
    between A and B at t" built on it (1.5 to 3.3 ms for 1,000 probes per pull). NPCs are looked up by
    instance, with their latest swing or cast as the sample. Callers decide which age is too old. At a run
    speed of 7 yards/s, 0.5 s is 3.5 yards, enough to matter against a 5-yard radius.
- **Legacy comparison (M2.6, 2026-10-11)**: `docs/legacy-comparison.md`, run with the legacy tool live against the
  seven fixture pulls. Deaths agree exactly (136 of 136); every other difference is a legacy defect. It found four
  defects not in the plan's list (no cooldown is ever matched, pre-death windows hold only self-damage, killing
  blow and overkill always blank, ability-table IDs always 0), and one defect both sides share (the killing ability
  name, fixed on 2026-10-16).
- **Real-fixture goldens (2026-10-11)**: `tests/unit/encounter/test_real_fixtures.py` snapshots the first 20
  normalized events and the full digest of every recorded fixture pull (`tests/golden/{normalized,digests}/
  <code>_<fight>.json`), using the encounter files and registry under `config/`, and checks every rendered digest
  stays under 3,000 tokens. It runs in the unit suite (about 10 s) and skips when the Git LFS pages are not pulled.
  An edit to an encounter file or the registry changes these goldens; regenerate with `--update-goldens` and review
  the digest diff. Fixture ingest (`tests/helpers/encounter/real_fixtures.py`, shared with the perf tests) reads enemy Casts
  unfiltered, so editing an encounter file's ability IDs never leaves the fixtures without that data type.
- **Death rows take the killing blow (2026-10-16)**: WCL Deaths rows carry `abilityGameID: 0` and `sourceID: -1`,
  so every death record named "Unknown Ability (0)". Normalize now sets `ability_id` from `killingAbilityGameID`
  when `abilityGameID` is 0 or missing and `source_id` from `killerID` when `sourceID` is -1; there is no schema
  change. `death_records` treats a death ability of 0 as unknown, so events normalized earlier fall back to the
  last hit. This changes `events_hash` for every real pull, which invalidates cached findings; stored nights need
  an offline re-ingest. SYNTH01's death rows now use WCL's shape so the synthetic fixture exercises the same path.
- **Report metadata refresh (2026-10-16)**: `ingest --refresh-meta` (and `/ingest --refresh-meta`) refetches only
  `report.json`, then re-normalizes the pulls already in the store from the raw page cache and rewrites those
  whose metadata or events changed. It never requests event pages: fights not stored yet are left alone (named
  fights not stored are reported as failed), and a pull whose pages are missing from the cache fails. It needs
  WCL, so it is a usage error with `--offline`.
- **WCL phases and phase reached (2026-10-16)**: `GET_REPORT_META` requests `report.phases` and each `Pull`
  carries its encounter's `wcl_phases` (id, name, is_intermission; empty by default, so old `pulls.json` files
  load). The query's `encounterID` field was confirmed on 2026-10-17, when `--refresh-meta` refetched the three
  local reports. `phase_reached` stays the last detected
  encounter-file phase ID; without an encounter file it is WCL's name for the last `phase_transitions` entry, or
  its ID as text when unnamed. `phase_trigger_unmatched:<id>` (`annotate.unmatched_phases`) is raised for an
  undetected encounter-file phase whose `wcl_phase` trigger ID is among the pull's transitions; for undetected
  phases with other triggers, it is raised in file order for as many phases as the pull has distinct WCL
  transitions beyond the phases detected. `last_phase` is no longer read by any digest logic, and a kill no
  longer raises the anomaly by itself: a kill whose transitions match the detected phases is consistent.
  `/pulls` shows "Phase reached" (the WCL name) in place of "Last phase". Re-ingested offline, none of the 47
  recorded Heroic Ula'tek pulls (the ones with an encounter file) raises the anomaly.
- **Positions stored and used for blame (2026-10-17)**: implements "Positions: keep them, as a per-pull table".
  Contract text is in `docs/plan-changes.md` under `encounter/positions`.
  - **Table and hash.** Normalize collects samples in the same pass as the events
    (`normalize_pull_with_positions`; `normalize_pull` is unchanged), so `events_hash` and the normalized goldens
    stay as they were. The store writes `events/<report>/positions/<fight>.parquet` and records `positions_hash`
    in `pulls.json`; the hash function lives in `store.py` next to `POSITION_SCHEMA`, and the store hashes what
    it writes. A missing `positions_hash` makes ingest rewrite the pull, which is how stored nights get their
    tables. Measured on a copy of the three local nights, offline: all 62 stored pulls rewritten with
    `events_hash` unchanged, a second run skips all 62; the 47 Heroic Ula'tek tables hold 2,022,724 samples,
    11.4 MB against 38.7 MB of events (+30%), all on map 2610; normalize goes from 3.3 to 4.0 s, 5.0 to 6.6 s
    and 4.2 to 5.6 s per night (+0.8 to +1.6 s).
  - **Lookup helper.** `analysis/positions.py`: `position_at` (latest sample at or before `t`, with its age;
    callers pass `max_age_ms`, None for no limit) and `distance_yd` (None across maps). Blame needs a handful of
    lookups per pull, so it filters the table directly rather than using `join_asof`.
  - **State rules.** `no_holder` first, then `dead` (a Deaths row at or before the first hit and no cast,
    begincast or resurrect by or on the player since; a later death counts again), then positions. Distance
    and age are recorded only for living players with a position: a dead holder's last sample says where they
    died, not where they were at the hit. A missing NPC sample, or samples on two maps, is `no_position`.
  - **Age limit 1,500 ms for the player, none for the NPC.** The investigation saw living holders' samples up
    to 475 ms old; at the first hit (rather than the cast) the oldest was 484 ms for Rattler Slam, 100 ms for
    Unchecked Rage and 520 ms for Mother's Wrath targets. Bosses stand on fixed spots and are only sampled on
    their own swings and casts, so their latest sample is used however old.
  - **Reach values.** `reach_yd` is the p95 of each NPC's own swing distances rounded up, measured over the 47
    Heroic pulls from the swing row's NPC sample to the target's latest sample at most 1 s old: Ula'tek n=932,
    p95 20.4 yd, max 30.3 yd, so 21; Gore Rattle n=399, p95 17.1 yd, max 30.7 yd, so 18. 330 Ula'tek and 122
    Gore Rattle swing rows carry no NPC sample and were left out. These match the investigation.
  - **Every Unchecked Rage cast is a failure.** Ula'tek recasts it every 2.4 s while she stays untanked, so its
    `cluster_window_ms` went from 3,000 to 1,000, which keeps each cast its own cluster. One cast's hits land
    within 60 ms of each other, and the next cast's hits come 2,416 ms or more later. Each Rage hurts the raid
    again, so each counts. Over the 47 pulls that gives 21 failures (was 11): 9 `beyond_reach` (30.9 to 54.9
    yd, judged at each cast's first hit; 55.5 yd when judged at the cast itself), 10 `dead`, 2 `no_holder`,
    the investigation's split. Rattler Slam: 10 failures, 6 `beyond_reach` (21.1 to 51.9 yd), 1 `dead`, 3
    `no_holder`, as in the investigation. No living holder was in reach.
  - **Mother's Wrath has no `reach_yd`.** Its 13 failures: 2 targets dead, 11 alive at 18.0 to 75.1 yd from
    Ula'tek's latest sample. One living target was within her 21-yard reach (18.0 yd) and three more within her
    longest swing of 30.3 yd (22.6, 25.1 and 29.6 yd), so the split is not clean; its failures show `alive` with
    the distance.
  - **Cache keys.** The annotation cache sidecar stores `positions_hash` and misses when it differs;
    `ANNOTATION_VERSION` is 4. `PullDigest.produced_from` carries `positions_hash`; `findings.cache_key` takes it
    (via `review_graph.pull_cache_key`), and `EncounterData` keys its per-pull records on it, so cached
    annotations, records and reviews rebuild when positions change.
  - **Dumps.** The new `MechanicStat` and `MechanicCluster` fields are left out of dumps when empty or null
    (pydantic `exclude_if`), so mechanics without blame context serialize as before, and stored digests and
    findings load with the defaults.
  - **Pulls that span several maps (2026-10-18).** `mapID` is the UI map a unit's client was on, and an
    encounter can move part or all of the raid to another floor or area mid-pull, where coordinates share no
    frame with the first map. Nothing measures across maps (`distance_yd` returns None, as before). Blame now
    takes each NPC unit's latest sample (`latest_per_unit`), keeps the units whose latest sample is on the
    blamed player's map, and measures from the latest of those. Before, it took the latest sample of any NPC
    unit, so one add on another map hid one on the player's; filtering samples by the player's map instead
    would measure from where an NPC last stood before it left. Position dedup also keys on `map_id`.
    `ANNOTATION_VERSION` is 6. None of the 62 local pulls or the recorded fixtures has samples on two maps
    (every pull is on 2610, or 2632 for `H7z1fqDXjRTFnbtg:23`), so results on them are unchanged and the
    multi-map case is covered by unit tests only.
- **Stack and aura failure rules (2026-10-17)**: two new failure rules.
  - `debuff_stacks_gt {value}` (value >= 0): the mechanic's friendly `applydebuffstack` rows, and `applydebuff`
    rows that carry a stack, with `stack` above `value`, grouped into occurrences by the mechanic's
    `cluster_window_ms` gap. Each occurrence is one failure on its first row, charged to no one (like
    `soak_count_lt`): the stacks come from enemies left alive, not from a player standing somewhere. The rows are
    not hits, so they belong to no cluster. Ula'tek's Acidic Expulsion (1313531) uses `value: 2`; across the 47
    recorded pulls its stack rows reach 2 (65 rows), 3 (3) and 4 (1), all of the last two on
    `trMgApZCVYkv3jdq:26`, which gives 1 failure.
  - `hit_while_aura {aura_ids}` (non-empty): each player hit by the mechanic's damage while carrying any listed
    aura, judged once per cluster at their first hit and charged to the player hit. An aura is active from its
    apply (or a refresh or stack row with nothing open) to the matching remove, both ends inclusive, or to the
    pull end if never removed; a remove with no apply before it counts from the pull start. Aura IDs may also be
    in the mechanic's `ability_ids`; only damage rows are judged. No encounter uses it yet.
  - Validation: `aura_ids` is required by `hit_while_aura` and rejected on every other rule; `blame` stays
    rejected outside `per_cluster`. `ANNOTATION_VERSION` is now 5, and the optional `aura_ids` field changes
    every encounter file's hash once (the digest goldens differ only in `encfile_hash`).
  - **Soul Constrictor has no detectable signal**, so Ula'tek gets no rule for it. Over the 47 recorded pulls
    (209 Spectral Coils occurrences, Coils damage 1287265 clustered with its 3 s window), Soul Constrictor is
    aura 1300685: 2,400 `applydebuff` and 2,400 `removedebuff` rows, no refreshes or stacks, each lasting about
    6 s (median 6,001 ms) and landing 0 to 5 ms before the player's Coils hit. Coils within a burst are 3.1 to
    4.5 s apart, so a soaker is still debuffed at the next one.
    - Debuff count: the share of the raid receiving it per occurrence is not a steady half. The median is 45%,
      but it ranges from 0 to 95%: the first Coils of most pulls debuffs 20 of 21 or 21 of 22 players, and some
      pulls alternate two groups of about 10 (`H7z1fqDXjRTFnbtg:18`: 11, 10, 10, 11). It is never applied to a
      player who already has it (0 of 2,400), so a debuffed player soaking again leaves no aura row.
    - Hit signal: one damage ID for everyone, and every hit in an occurrence is the same size (the ratio to the
      occurrence median is 0.97 to 1.03 at the 5th and 95th percentiles, for players newly debuffed, already
      debuffed or neither). No other aura marks soakers.
    - `hit_while_aura` on 1300685 would charge all 699 hits on players still debuffed from the previous Coils,
      soakers or not, so it isn't added. The item stays in `docs/follow-ups.md`.
- **M2 exit criteria waiting on humans**: review of the H3 encounter file. Digest goldens, the token check, the perf
  budgets and the legacy comparison are done.

### M3 (analyst loop)

Plan v0.3.2 (2026-10-11) folds the contract changes below into its section 6 and decisions D5/D11.

- **Refs resolve against uncapped records**: `digest.build_pull_records` returns every death, mechanic, cooldown and
  window (`PullRecords`) plus the capped digest; `build_digest` wraps it. A ref beyond a digest cap (for example the
  11th death) still resolves, and query tools can return it.
- **`EncounterData`** (`evidence.py`) is the read side for analysis: the store plus the encounter-file root,
  registry, annotation cache and cutoff fraction. It caches `PullRecords` in-process (bounded LRU keyed by
  pull_key, `events_hash` and `encfile_hash`). `resolve(ref, data)` takes `EncounterData` or a bare `EventStore`
  (which then uses `config/` files), instead of the plan's `resolve(ref, store)`.
- **What refs resolve to** (contract detail not in plan 6.5): `pull` → a summary (outcome, duration, boss %, phase
  reached, cutoff, death counts, coverage, anomalies); `mech:<id>` → the `MechanicStat` plus `cluster_rows`
  (`MechanicCluster`: per-cluster first/last hit, hits, players, damage, failures); `event:<seq>` → the annotated
  event with actor and ability names instead of actor IDs, nulls dropped, `hp_pct` rounded to 0.1. Query tools
  return exactly these dicts, so verify checks claims against what the model saw. `plan:` refs parse but do not
  resolve until raid plans exist.
- **Claim numbers** also accept `2:14` (m:ss), `12.4 seconds`, `250ms`, `1.2k` and `3.4m`; digits inside words
  (`Healbot2`, `p2`, `3rd`) and inside refs are ignored. A number matches if a record value, scaled to the claim's
  unit and rounded to the claim's precision, equals it.
- **Query tool shapes** (plan 6.6 names tools and args only): rows are the resolved record for their ref, or a
  subset of its fields (`list_pulls` and `compare_pulls` rows are slim pull summaries). `query_mechanic` returns
  `{"mechanic": stat, "clusters": [...]}`; cluster rows carry the mechanic's ref. `cooldown_timeline` returns
  cooldown uses and damage-window rows in one list sorted by time; a window's phase is the phase active at its
  first hit. `compare_pulls(metric="mechanic_failures")` rows carry the `mech:` ref. A bare fight ID is accepted
  as `pull` when exactly one pull in scope has it.
- **`get_digest` is exempt from the 4,000-character cap**: the rendered digest targets 1.5k-3k tokens, so the
  digest's own caps bound it instead.
- **`submit_report` arguments** are `findings` (claim, refs (min 1), confidence) and `unknowns`. The finding
  `id`, `scope`, request ID and `produced_by` are filled in by code, not the model.
- **`prompt_version` hashes prompt templates, not rendered prompts.** The rendered analyst prompt embeds the
  pull's digest, which would make the version differ per pull; the findings cache key already carries
  `events_hash` and `encfile_hash`. It also hashes the role and each tool's `tool_call_schema` (the schema the
  model is sent, without the injected run config) rather than `args_schema`. `VERIFY_RULES_VERSION` lives in
  `versioning.py` next to `GRAPH_VERSION`.
- **Verify rule additions** (plan 6.7, still `VERIFY_RULES_VERSION = 1` as the first implementation): a ref
  must resolve to a pull inside the request, not just any ingested pull; and the number of refs cited (overall
  and per kind) counts as a supported value, so "3 players died" citing three death refs passes. Numbers come from
  every field of the cited records, including IDs and digits inside strings, so a small count can match by
  coincidence; definitions that are not recorded values (the 90% time-to-die threshold) are rejected.
- **Rejection reasons** are recorded as `<finding id> "<claim>": <reason>`.
- **Analyst graph wiring**: `build_analyst_graph()` takes no store. The data, model, model ID, prompt version
  and tools travel in `config["configurable"]["analyst"]` (`AnalystRun`), never in graph state, and the tools node
  adds the `AnalystScope` to the config it passes to tools. `run_analysis(req, *, store, callbacks, run_id)`
  accepts `EncounterData` or a bare store, calls `llm_factory.get_llm(role="analyst")` once per run, and traces the
  run as `encounter_analyst` with the request ID, versions, model ID, pulls and depth as metadata. `run_id` lets the
  eval harness attach feedback to the trace.
- **Analyst state** adds `forced` (budget hit), `pulls` (requested pulls that are ingested) and `unknowns` (from
  code) to the plan's fields. Requested pulls that are not ingested become `pull <key> is not ingested` unknowns;
  if none are ingested the model is never called.
- **Step accounting**: a `submit_report` round costs a step like a query round. The single retry (for rejected
  findings or an invalid `submit_report` call) happens only if steps remain after the submission; otherwise the
  rejections stand. A forced final turn that calls anything but `submit_report` ends with
  `unknowns=["no structured report"]` and `partial=True`. Other tool calls made alongside `submit_report` are
  answered "ignored" and not run.
- **Golden eval harness**: question schema, loading and the deterministic checks live in `tests/helpers/encounter/golden.py`
  (unit-tested on SYNTH01); `tests/eval/` runs them on a real analyst model. Eval tests carry an `eval` marker and,
  like `perf`, are skipped unless selected (`-m eval`; `mise run test:eval` passes it). Recorded reports the questions
  name are ingested offline into a session store. The pass rate per model and ref-resolution counts are printed
  at the end and written to `$KHADBOT_DATA_DIR/eval/golden-<timestamp>.json`. `KHADBOT_EVAL_MIN_PASS_RATE` gates
  the run once thresholds are set (unset: report only); `KHADBOT_GOLDEN_QUESTIONS` points at another question
  file. With `LANGSMITH_API_KEY` set, each question's result is attached to its traced run as `golden_pass`
  feedback (the "upload" the plan mentions). Validation is stricter than the plan's schema: `unanswerable`
  requires `must_be_unknown: true`, and answerable questions need `expected_refs_any`. An optional `depth` field
  selects the step budget.
- **M3 exit criteria waiting on humans**: golden questions (H5; `tests/golden/questions.yaml` is an empty list with
  the schema in comments), thresholds and the Groq gate decision (H6), and the query-tool p95 perf test (written,
  skipped until H2 fixtures exist). Unanswerable handling and every scripted scenario pass on SYNTH01.

### M4 (findings persistence and fan-out)

- **Started ahead of the M3 gate** (2026-10-11): the M3 exit needs the golden questions, thresholds and the Groq
  gate decision (H5, H6), which are still open. M4 is built and tested on SYNTH01 with fake models. If the gate
  changes the analyst's tools or prompts, the cache key changes with them (by design), but the synthesis prompt
  and the cross-pull golden check should be revisited.
- **What is cached**: only the per-pull review, under `findings.cache_key` (plan 6.8). The synthesis runs on every
  review because it answers the user's question; it is cheap next to the per-pull analyses. So "a second identical
  request makes no model calls" holds for per-pull analysis, not for the synthesis call.
- **Per-pull review question** is the fixed `STANDARD_REVIEW_QUESTION`. It is hashed into the review's
  `prompt_version` (`review_prompt_version()`), which is what the cache key carries; editing it invalidates the
  cache. Synthesis has its own `synthesis_prompt_version()` (role `synthesis`, tools `compare_pulls` and
  `submit_report`), recorded in the final report's `produced_by`.
- **Not cached**: a per-pull report that is `partial` or has `no structured report` is used for this review but
  not stored, so a transient failure is retried next time. The final report is marked `partial` in that case.
- **Flagging** is per stored row: `FindingsStore.flag(finding_id)` flags the `PullFindings` that contains the
  finding. `get` returns flagged rows (with `flagged=True`); the review treats a flagged row as a cache miss,
  re-analyses the pull, and the new row replaces the flagged one under the same key. `for_pull` hides flagged rows
  unless asked.
- **Synthesis wiring**: reuses the analyst graph with `AnalystRun.synthesis` set to the rendered per-pull
  findings, so verify (refs inside the request, numbers match records, one retry) applies unchanged. All
  synthesis findings are forced to `scope="cross_pull"`. If no pull produced findings the model is not called and
  the report carries the pulls' unknowns.
- **Concurrency**: `review` passes `max_concurrency=EncounterConfig.analyst_max_concurrency` in the run config;
  the per-pull `Send` tasks respect it. Per-pull runs are traced as `encounter_analyst` children of
  `encounter_review`.
- **Cost estimate** (`estimate_review_cost`): `sum(len(rendered digest) / 4)` over the pulls plus
  `OUTPUT_TOKENS_PER_PULL` (1,500, an allowance rather than a cap) per pull, with no model call. It ignores
  cached pulls, so it is an upper bound. `/review` prints it before running and `--estimate` prints it only.
  The report code is `--report <code>` (default: the last ingested), because a positional code is ambiguous
  with the first word of the question.

### Event store spike (S2, 2026-10-11)

Outcome: **keep polars + parquet (D7 unchanged).** Measured on the dev machine over all 62 real pulls in
`KHADBOT_DATA_DIR` (three nights, up to 177k events per pull), with the Ula'tek encounter file present for the
47 Ula'tek pulls.

- **Annotate + digest one pull** (budget 2 s): `build_pull_records` cold, median 0.28 s, max 0.45 s
  (`H7z1fqDXjRTFnbtg:18`, 138k events, 26 deaths). Death records are the largest stage (median 135 ms, max
  280 ms: a per-death filter over the frame), then mechanics (53 ms) and annotate (36 ms).
- **Re-annotate a night** (budget 30 s): 0.6 to 1.4 s per night; a full digest of every pull is 3.7 to 7.8 s.
- **Rendered digests** with the encounter file: 1,310 to 2,089 tokens (`len / 4`), inside the 1.5k-3k target
  except the shortest pulls.
- **polars vs DuckDB** on the same 47 pulls, same results asserted for each pair (median / max ms): full parquet
  load 10.5 / 24 vs 34 / 69; filtered read (30 s window, three types) 7.5 / 16 vs 28 / 39; gap clustering
  4.6 / 6.6 vs 50 / 74; aura-interval pairing 1.9 / 4.1 vs 103 / 166; death windows (range join in DuckDB)
  137 / 235 vs 144 / 202. DuckDB is not faster on anything that matters, so there is no reason to switch.
- **Query tool p95** (budget 200 ms) first measured 283 ms to 1.1 s on the longest pull of each night, but not
  because of the store: in `query_events` the window filter took about 1 ms and the rest was building a model
  row for every event in the 30 s window (8k to 22k) before the envelope kept 25. `query_events` now builds rows
  only for the events the envelope can keep and passes the full count as `total`; p95 is now 7 to 77 ms.
- **The `-m perf` tests** ingested the real fixture reports without fight IDs and failed on the fights with no
  recorded pages. They now ingest only the recorded fights; all 12 pass.

### Dashboard API (2026-10-16)

The read API the digest dashboard is built on, in `src/khadbot/api/`. It serves the encounter pipeline's records;
it adds no analysis of its own beyond the night aggregates.

- **Stack and serving.** FastAPI with uvicorn. `python main.py serve [--port 8000]` binds to `127.0.0.1` only and
  reads `KHADBOT_DATA_DIR`; CORS allows only `http://localhost:3000` and `http://127.0.0.1:3000`, GET only. WCL
  credentials stay in the server's environment and nothing in this API calls WCL or an LLM. The frontend is
  Next.js with client-rendered data views fetching from this API (no Next.js route handlers or server data
  fetching), so there is one backend.
- **Contracts as built.** Refs are the `ev:<report>:<fight>:<kind>...` grammar `evidence.py` parses, URL-encoded
  in paths and `?ref=`. Coverage is `full | partial | none`. `PullDigest` is returned unchanged. URLs address a
  pull by WCL fight ID; the ordinal within its encounter-and-difficulty group (`P1`...`Pn`) is a display label.
  `contracts/openapi.json` is generated from the app (`mise run gen:contracts`, or `uv run python scripts/gen_contracts.py`) and a unit test
  fails on drift.
- **`?full=true` digest** keeps the `PullDigest` shape and the digest's ordering but replaces the capped lists
  with every record: every death, every mechanic in the encounter file (including ones with no hits), every
  cooldown use and window. The unregistered list stays capped, since the records hold only the capped one.
- **Evidence records and event rows are untyped objects** in the contract (`record: object`, `rows: object[]`):
  they are exactly the dicts `EncounterData.resolve` returns, so the browser sees what the verify step checks.
  `kind` is the published `RefKind` enum.
- **A night is a whole report**, one section per encounter and difficulty in the order first pulled. Section
  coverage is `none` without an encounter file for that difficulty, `partial` if any pull is partial, else
  `full`. Best pull: the first kill, else the wipe with the lowest boss health (ties: lower fight progress).
  The most common first death is counted over every pull in the section ("on N of M pulls", M = all pulls).
- **Briefing numbers pass the verify check.** Each line cites refs such that every number in it is in a
  resolved record or is a ref count, the same rule `check_finding` applies: "on N of M pulls" cites N `death`
  (or `mech`) refs and the M `pull` refs. That rules out sums and medians, so the mechanic line reads "Y was
  failed on N of M pulls, most on Pk (n failures)" instead of "Y failed N times across M pulls": a night's total
  failures is in no single record. The night total is still in the KPI and the mechanic table, with its refs.
- **Timeline mechanic markers** are clusters; clusters have no ref of their own, so each carries its mechanic's
  `mech` ref plus the cluster number.
- **Events** take `actor` as a name or an actor ID and reject an unknown actor (`unknown_actor`); windows over
  30 s or reversed are `bad_window`; rows cap at 200 with `truncated`, `total` and a note. Request validation
  errors use the same `{code, message}` body (`bad_request`, 422) as every other error.
- **Findings** come from `FindingsStore.for_pull` (flagged rows left out). A row is `stale` when its recorded
  `events_hash` differs from the store's or its `encfile_hash` differs from the current difficulty view's.
  Reading never creates the SQLite file.
- **Concurrency.** Handlers run in FastAPI's thread pool and hold one lock while they build records, because the
  `EncounterData` LRU is shared. Fine for one local user; see follow-ups for large reports.
- **Fixture data.** `scripts/build_fixture_data.py --out <dir>` (`mise run gen:fixture-data`) ingests the recorded
  fixture reports offline through `ingest.recorded` (with the Buffs filter the fixtures were recorded with,
  `RECORDED_BUFF_IDS`), the same code the test helpers use, and copies each
  `report.json` so titles show. Playwright and local demos serve that directory.

### Blob storage seam (2026-10-20)

The raw page cache, the event store and the annotation cache read and write through a `BlobStore`
(`src/khadbot/storage/`), so a later object-store implementation can replace the data directory without touching
the stages.

- **Protocol.** `get(key) -> bytes | None`, `put(key, data)`, `exists`, `list(prefix)`, `delete`. Whole objects
  only: parquet goes through `BytesIO`, and the event store reads the whole events object before filtering
  instead of scanning the file. `put` is atomic. `list` is recursive, sorted, and treats the prefix as a string
  prefix, the way S3 does. Deleting a missing key is not an error.
- **Keys mirror the data directory.** `raw/<report>/…`, `events/<report>/…`, `annotated/<report>/…`, exactly the
  old paths relative to `KHADBOT_DATA_DIR`, so `LocalBlobStore` over an existing directory reads it unchanged and
  writes the same bytes. Keys are `/`-separated and relative, and may not hold empty, `.` or `..` segments,
  backslashes, colons or other characters a Windows path can't hold, or end in `.tmp` (the suffix of an
  in-progress local write, which listings skip). An invalid key raises `InvalidKeyError`, a `ValueError`; the API
  answers a report code no key can hold with `report_not_found`, and an evidence ref with one is unresolvable.
- **One factory.** `storage.default_blob_store()` returns `LocalBlobStore(KHADBOT_DATA_DIR)`; `default_cache`,
  `default_store`, `default_annotation_cache`, `EncounterData.from_config` and `create_app` all use it, and
  `create_app` takes `blobs=` too. Each persistence class takes a store plus a key prefix (`raw`, `events`,
  `annotated` by default) and also still accepts a directory path, meaning a store rooted at that stage's
  directory with no prefix. The recorded fixtures (a raw layout without `raw/`), the scripts and most tests use
  that form unchanged.
- **Local writes.** `atomic_write` writes a uniquely named `*.tmp` sibling and renames it over the target, so two
  writers never share a temporary file. On Windows a rename onto a file a reader has open, or an open during a
  rename, fails with a sharing violation; both sides retry for up to about a second (2 ms doubling, 10 tries).
  The contract suite's concurrent read-while-overwrite test failed without the retry.
- **Contract tests.** `tests/helpers/storage/contract.py` holds the behaviour every store must have;
  `tests/unit/storage/test_blob_contract.py` runs it for each entry in `BLOB_STORES`, so another implementation
  joins by adding an entry, and an integration suite can subclass the same class.
- **Compatibility.** `tests/golden/storage/data_dir_manifest.json` is the SHA-256 of every file under `events/`
  and `annotated/` after ingesting and digesting the recorded fixtures (3 reports, 7 pulls, 37 files). It was
  produced by the path-based classes from before this change; the blob store matches it file for file, rewrites
  the same bytes over an existing directory, and reads events, positions, actors, abilities and annotations as
  `polars` reads the files. Digests built through it match the existing digest goldens.
- **Timings.** Offline ingest and digest of the largest recorded night (`H7z1fqDXjRTFnbtg`, 4 pulls, 126 MB of raw
  pages) through the default factories, 10 runs each, before and after interleaved, medians:

  | Step | Before | After | Change |
  |---|---|---|---|
  | Offline ingest into an empty data directory | 4.44 s | 4.39 s | -1% |
  | Offline re-ingest (every pull skipped) | 3.94 s | 3.79 s | -4% |
  | Digest of every pull, annotation cache empty | 1.72 s | 1.75 s | +2% |
  | Digest of every pull, annotation cache warm | 1.29 s | 1.35 s | +4% |

  Run-to-run spread on this Windows machine is 10 to 30%, larger than any of the changes.

## Human task status

| ID | Task | Status |
|----|------|--------|
| H1 | Choose boss + three public reports | done 2026-10-11: Heroic Ula'tek (encounter 3492). Wipe-heavy night `trMgApZCVYkv3jdq`; clean kill `H7z1fqDXjRTFnbtg` fight 20, with the rest of that report as the messy night; second messy / early-learning night `QkVa6GHRK8yfrTbw`. See `docs/wcl-api.md`. |
| H2 / S1 | Record real fixtures, run cost spike | done 2026-10-11: all 62 boss pulls ingested into `KHADBOT_DATA_DIR` (936 points); seven representative pulls copied to `tests/fixtures/encounter/wcl/` and stored in Git LFS (see its README); measurements in `docs/wcl-api.md`. |
| H3 / S3 | Author the encounter file for the H1 boss | first pass 2026-10-11, awaiting human review: `config/encounters/53-the_venomous_abyss/3492-ulatek.yaml`. Mechanics from the in-game encounter journal (Wowhead NPC page) and the Method, Wowhead and MythicTrap Heroic guides (Wipefest's Guide tab embeds MythicTrap's guide; Wipefest's own mechanic tagging is not used); every ability ID taken from the recorded pulls. Phases use `wcl_phase` triggers. Across all 47 recorded Ula'tek pulls: coverage `full`, unregistered damage at most 3.3%, all of it player damage such as Spirit Link and Burning Rush, and none since the friendly-damage registry (2026-10-18), no `phase_trigger_unmatched`, re-annotating every pull takes about 17 s. Deadly Venom (venom pools), Toxin Cloud (early step onto the stage-two platforms) and Ingested Venom (Serpent's Bite soak debuff, believed) identified by the raid. Raid punishments now use the blame rules: Mother's Wrath (raid) is charged to the cast's target, Unchecked Rage and Rattler Slam to Ula'tek's or Gore Rattle's aggro holder (latest swing or taunt), Calcified Corpse to the bitten player, Circling Prey to hits over 1M before mitigation, and Desperate Thrash to anyone other than the casting add's aggro holder. Across the 47 pulls that gives 13, 21, 10, 19, 12 and 28 failures (Unchecked Rage counts each cast since 2026-10-17); 2 Unchecked Rages and 3 Rattler Slams have no aggro holder in the 30 s before and are charged to no one. Acidic Expulsion fails past 2 stacks (`debuff_stacks_gt`), once across the 47 pulls (2026-10-17); Soul Constrictor has no rule, since the logs don't show who soaked while debuffed. Mephitic Thrash still has no rule: the logs treat players inside and outside it the same. Open items for this file are under Ula'tek in `docs/follow-ups.md`. |
| H4 | Fill cooldown durations in `defensive_cooldowns.yaml` | open |
| H5 / S4 | Write golden questions | open |
| H6 | Set eval thresholds | open |
| H7 | Update external engineering-principles doc | open |

## Findings from the real reports (2026-10-11)

From the fight lists of the three reports and the first live ingest. The API facts behind these are in
`docs/wcl-api.md`; the items below are what they change in the encounter code. Each still needs a code change
and, where marked, a contract update in plan section 6.

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
  `docs/wcl-api.md`. Players count stages, so "phase 3" on Ula'tek is phase ID 4 (Stage Three), which started 362
  to 365 s into every pull that reached it. Use these IDs in `config/encounters/53-the_venomous_abyss/3492-ulatek.yaml`; a `wcl_phase` trigger
  works for every Ula'tek phase. The enrage is *Fury Unleashed* (confirmed by the raid leader).

## Log investigations (2026-10-16)

Three questions answered from the recorded Heroic Ula'tek pulls (47 pulls, three nights), read from the raw
pages with scratch scripts and no WCL calls. The API behaviour behind them is in `docs/wcl-api.md` (Events, and
Instances and positions). The proposed contract text is in `docs/plan-changes.md` under
`encounter/spike-logs`; none of it is implemented.

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
on one long pull, recorded in `docs/wcl-api.md`.

## Friendly damage on the recorded nights (2026-10-18)

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

## Open follow-ups

Moved to `docs/follow-ups.md` (2026-10-11), which tracks open work for the encounter system and for each
encounter file.
