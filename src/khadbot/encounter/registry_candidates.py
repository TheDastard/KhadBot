"""
encounter/registry_candidates.py

Propose cooldown-registry and friendly-damage-registry entries from SimC client data (``khadbot.simc``),
and check the registries' entries against that data.

A cooldown candidate is a player spell with a cooldown of at least ``min_cooldown_s`` whose own effects, or
the effects of a spell it triggers, do one of these things:

- defensive: apply less damage taken, an absorb, an immunity, more maximum health, split damage, more
  healing taken or a cheat-death absorb;
- healing: heal an area, or heal one target for a share of its maximum health;
- support: put any other aura on one chosen ally (Innervate);
- offensive: raise damage done, haste, critical strike, mastery, versatility, a primary stat or auto-attacks
  (Bloodlust, Avenging Wrath, Power Infusion), or add to other spells' damage or critical strikes through a
  spell modifier (Recklessness, Combustion, Shadow Blades).

The first three make a defensive-cooldown candidate (``defensive_candidates``), the last an offensive one
(``offensive_candidates``). On a spell with an offensive effect, a defensive aura shorter than 2 s from a
spell the cast triggers (a leap's landing immunity) and any support aura don't make it a defensive
candidate, and two offensive cooldowns whose defensive effects the data can't set apart are skipped by name
(``_NOT_DEFENSIVE``). Its ``target`` comes
from who the effect lands on: only the caster is ``personal``, one chosen ally is ``external``, an area or
the raid is ``raid``. Casts that work through an area trigger, a totem or a scripted effect fall back to
same-name spells for their aura. All of this is a heuristic; every candidate is unverified until a human
checks it.

A utility candidate (``utility_candidates``) is a player cooldown that helps allies move, cast or come back
(speed, mana, resurrection, root and snare immunity, moving an ally) or controls enemies (stun,
incapacitate, root, slow, grip, knockback). Its ``target`` is personal, external or raid for allies, and
enemy or enemies for one enemy or an area. SimC's tables keep only the effects its simulations read, so a
spell may have lost the effect that matters (Rebirth's resurrection, Blind's disorient);
``dropped_effects`` names the gaps.

A friendly-damage candidate (for ``config/registries/friendly_damage.yaml``) is any spell with a class
mask, player spell or not, with a damage effect or damage aura that lands only on the caster or allies:
Stagger, Burning Rush, Shadow Word: Death's backlash. The data can't tell shared-out damage from
delayed boss damage or a chosen cost, and it misses damage done in server scripts (Blessing of
Sacrifice, Time Dilation), spells SimC leaves out of its tables (Spirit Link's damage) and item or
borrowed-power effects (class mask 0), so recorded logs stay the main way to find these.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from typing import Protocol

from khadbot.simc import codes as c
from khadbot.simc.client_data import CLASS_NAMES, ClientData, Effect, Spell, class_names

# ---------------------------------------------------------------------------
# Heuristic sets
# ---------------------------------------------------------------------------

# Auras that make a player harder to kill. A_MOD_DAMAGE_PERCENT_TAKEN and A_MOD_AOE_DAMAGE_AVOIDANCE count
# only with a negative value (less damage), A_MOD_HEALING_RECEIVED_PCT only with a positive one.
DEFENSIVE_AURAS = frozenset(
    {
        c.A_MOD_INCREASE_HEALTH,
        c.A_SCHOOL_IMMUNITY,
        c.A_DAMAGE_IMMUNITY,
        c.A_SCHOOL_ABSORB,
        c.A_SPLIT_DAMAGE_PCT,
        c.A_MOD_DAMAGE_PERCENT_TAKEN,
        c.A_MOD_HEALING_RECEIVED_PCT,
        c.A_MOD_AOE_DAMAGE_AVOIDANCE,
        c.A_MOD_INCREASE_HEALTH_2,
        c.A_SCHOOL_ABSORB_OVERKILL,
    }
)
_NEGATIVE_ONLY = {c.A_MOD_DAMAGE_PERCENT_TAKEN, c.A_MOD_AOE_DAMAGE_AVOIDANCE}
_POSITIVE_ONLY = {c.A_MOD_HEALING_RECEIVED_PCT}
# Auras that make a player deal more damage; each counts only with a positive value.
OFFENSIVE_AURAS = frozenset(
    {
        c.A_MOD_DAMAGE_PERCENT_DONE,
        c.A_HASTE_ALL,
        c.A_MOD_MELEE_RANGED_HASTE,
        c.A_MOD_MELEE_HASTE,
        c.A_MOD_RANGED_HASTE,
        c.A_MOD_CASTING_SPEED_NOT_STACK,
        c.A_MOD_ALL_CRIT_CHANCE,
        c.A_MOD_CRIT_PERCENT,
        c.A_MOD_MASTERY_PCT,
        c.A_MOD_VERSATILITY_PCT,
        c.A_MOD_TOTAL_STAT_PERCENTAGE,
        c.A_MOD_PERCENT_STAT,
        c.A_MOD_RANGED_AND_MELEE_AUTO_ATTACK_SPEED,
        c.A_MOD_AUTO_ATTACK_PCT,
    }
)
# Spell-modifier properties that make a player deal more damage, each only with a positive value: a percentage
# on damage (P_GENERIC, an effect's value, periodic damage) or critical damage, and a flat bonus to critical
# strike chance. Most damage cooldowns work this way rather than through a damage-done aura (Recklessness,
# Combustion, Shadow Blades, Pillar of Frost). The data doesn't say whether the spells they change deal damage
# or heal, so a healer's cooldown can show up too.
OFFENSIVE_PCT_PROPERTIES = frozenset(
    {c.P_GENERIC, c.P_EFFECT_1, c.P_EFFECT_2, c.P_EFFECT_3, c.P_EFFECT_4, c.P_EFFECT_5, c.P_TICK_DAMAGE, c.P_CRIT_BONUS}
)
OFFENSIVE_FLAT_PROPERTIES = frozenset({c.P_CRIT})
HEALING_AURAS = frozenset({c.A_PERIODIC_HEAL, c.A_PERIODIC_HEAL_PCT})
HEAL_EFFECTS = frozenset({c.E_HEAL, c.E_HEAL_MAX_HEALTH, c.E_HEAL_PCT})
# Heals that count on a single target too; a plain E_HEAL counts only when it lands on an area.
_BIG_HEALS = {c.E_HEAL_MAX_HEALTH, c.E_HEAL_PCT}
# Casts whose aura comes from a separate, unlinked spell (see _same_name_hits).
_INDIRECT_EFFECTS = frozenset({c.E_DUMMY, c.E_SUMMON, c.E_CREATE_AREA_TRIGGER})
_AREA_PLACEMENT = {c.E_SUMMON, c.E_CREATE_AREA_TRIGGER}
_DUMMY_AURAS = {c.A_DUMMY, c.A_PERIODIC_DUMMY}

# Damage that can land on a player: direct effects, and auras applied by E_APPLY_AURA or an area aura.
DAMAGE_EFFECTS = frozenset({c.E_SCHOOL_DAMAGE, c.E_HEALTH_LEECH})
DAMAGE_AURAS = frozenset({c.A_PERIODIC_DAMAGE, c.A_PERIODIC_LEECH, c.A_PERIODIC_DAMAGE_PERCENT})

# Single-target labels that can only be a friendly player; any other aura cast on one counts as support
# (Innervate). T_UNIT_TARGET_ANY can be an enemy, so it is left out.
_SUPPORT_TARGETS = {"T_UNIT_TARGET_ALLY", "T_UNIT_CASTER_TARGET_RAID", "T_UNIT_TARGET_ALLY_OR_RAID"}
# Targets that can only be the caster or a friendly player.
_FRIENDLY_TARGETS = set(c.TARGETS) - {c.T_UNIT_TARGET_ANY}

_TARGET_RANK = {"personal": 0, "external": 1, "raid": 2}
_ALLY_TARGETS = frozenset(_TARGET_RANK)
_TRIGGER_DEPTH = 2


def _seconds(ms: float) -> float | None:
    return round(ms / 1000, 3) if ms > 0 else None


# ---------------------------------------------------------------------------
# Cooldowns
# ---------------------------------------------------------------------------


@dataclass
class Candidate:
    spell_id: int
    name: str
    class_name: str
    target: str
    #: defensive (damage reduction, absorbs, immunities, cheat death), healing or support (any other aura
    #: cast on one ally) for a defensive-cooldown candidate; offensive for an offensive one
    role: str
    cooldown_s: float
    duration_s: float | None
    charges: int
    spec_ids: list[int]
    #: Spells that carry the defensive aura, when not the cast spell itself (what Buffs events show).
    aura_spell_ids: list[int]
    evidence: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class _Hit:
    effect: Effect
    role: str
    target: str
    what: str
    label: str

    def describe(self, *, same_name: bool = False) -> str:
        note = " (same-name spell)" if same_name else ""
        return f"{self.effect.spell_id}#{self.effect.index} {self.what} {self.label}{note}"


def _effect_target(effect: Effect) -> tuple[str, str] | None:
    """(target, label) from who an effect lands on, or None for enemies and anything unrecognised."""
    if effect.type in c.AREA_AURA_EFFECTS:
        return "raid", c.EFFECT_TYPES[effect.type]
    targets = {effect.target_1, effect.target_2} - {0}
    for t in targets:
        if t in c.AREA_ALLY_TARGETS:
            return "raid", c.AREA_ALLY_TARGETS[t]
    for t in targets:
        if t in c.SINGLE_ALLY_TARGETS:
            return "external", c.SINGLE_ALLY_TARGETS[t]
    if targets <= {c.T_UNIT_CASTER}:
        return "personal", "T_UNIT_CASTER"
    return None


def _offensive_modifier(effect: Effect) -> bool:
    """A spell-modifier aura that raises damage or critical strikes (``OFFENSIVE_PCT_PROPERTIES``)."""
    if effect.subtype not in c.SPELL_MODIFIER_AURAS or effect.base_value <= 0:
        return False
    flat = effect.subtype in c.FLAT_MODIFIER_AURAS
    return effect.misc_value in (OFFENSIVE_FLAT_PROPERTIES if flat else OFFENSIVE_PCT_PROPERTIES)


def _effect_hit(effect: Effect) -> _Hit | None:
    """How one effect helps the raid survive or deal damage, or None."""
    found = _effect_target(effect)
    if found is None:
        return None
    target, label = found
    if effect.type in c.AURA_EFFECTS:
        if effect.subtype in DEFENSIVE_AURAS:
            if effect.subtype in _NEGATIVE_ONLY and effect.base_value >= 0:
                return None
            if effect.subtype in _POSITIVE_ONLY and effect.base_value <= 0:
                return None
            return _Hit(effect, "defensive", target, c.AURA_TYPES[effect.subtype], label)
        if effect.subtype in HEALING_AURAS and target == "raid":
            return _Hit(effect, "healing", target, c.AURA_TYPES[effect.subtype], label)
        # Checked before support, so Power Infusion on an ally is offensive, not support.
        if effect.subtype in OFFENSIVE_AURAS:
            if effect.base_value <= 0:
                return None
            return _Hit(effect, "offensive", target, c.AURA_TYPES[effect.subtype], label)
        if _offensive_modifier(effect):
            what = f"{c.AURA_TYPES[effect.subtype]} {c.PROPERTY_TYPES[effect.misc_value]}"
            return _Hit(effect, "offensive", target, what, label)
        if target == "external" and label in _SUPPORT_TARGETS:
            return _Hit(effect, "support", target, f"aura {effect.subtype}", label)
        return None
    if effect.type in HEAL_EFFECTS and (target == "raid" or effect.type in _BIG_HEALS):
        return _Hit(effect, "healing", target, c.EFFECT_TYPES[effect.type], label)
    return None


_HitFn = Callable[[Effect], "_Hit | None"]


def _hits(data: ClientData, spell_id: int, depth: int = 0, hit_fn: _HitFn = _effect_hit) -> Iterator[_Hit]:
    """Hits from a spell's effects and from the spells it triggers, ``_TRIGGER_DEPTH`` levels down."""
    for effect in data.effects.get(spell_id, []):
        hit = hit_fn(effect)
        if hit:
            yield hit
        triggers = effect.type == c.E_TRIGGER_SPELL or (
            effect.type in c.AURA_EFFECTS and effect.subtype == c.A_PERIODIC_TRIGGER_SPELL
        )
        if triggers and effect.trigger_spell_id and effect.trigger_spell_id != spell_id and depth < _TRIGGER_DEPTH:
            yield from _hits(data, effect.trigger_spell_id, depth + 1, hit_fn)


def _same_name_hits(
    data: ClientData, spell: Spell, hit_fn: _HitFn = _effect_hit, indirect: frozenset[int] = _INDIRECT_EFFECTS
) -> list[_Hit]:
    """Hits found by ``hit_fn`` on other spells that share the cast's name.

    Area triggers, totems and scripted dummy effects apply their aura from a separate spell the data does not
    link to the cast (Power Word: Barrier, Anti-Magic Zone, Rallying Cry). That spell usually has the cast's
    name, so a spell whose effects find nothing falls back to same-name spells that are not player spells.
    """
    own = data.effects.get(spell.spell_id, [])
    if not any(e.type in indirect for e in own):
        return []
    # A cast that places something on the ground or targets an area helps everyone near it, whatever target the
    # linked aura itself names. Effects on enemies already name their own reach.
    area = any(e.type in _AREA_PLACEMENT or (_effect_target(e) or ("",))[0] == "raid" for e in own)
    out: list[_Hit] = []
    for other in data.by_name.get(spell.name, []):
        if other == spell.spell_id or other in data.player_spells:
            continue
        for hit in _hits(data, other, hit_fn=hit_fn):
            if hit.role == "support":
                continue
            target = "raid" if area and hit.target in _ALLY_TARGETS else hit.target
            out.append(_Hit(hit.effect, hit.role, target, hit.what, hit.label))
    return out


_DEFENSIVE_ROLES = frozenset({"defensive", "healing", "support"})
# On a spell with an offensive effect, a defensive aura from a spell it triggers that lasts less than this is a
# side effect, not a cooldown: Havoc's Metamorphosis (191427) is immune for 1 s on landing (201453). Cloak of
# Shadows' own 1 s immunity (35729) still counts, since Cloak has no offensive effect.
_MIN_TRIGGERED_DEFENSIVE_MS = 2_000
# Offensive cooldowns whose defensive effects the data can't tell apart from a defensive cooldown's (build
# 12.1.5.70448). Each is left out of the defensive candidates.
_NOT_DEFENSIVE: dict[int, str] = {
    19574: "Bestial Wrath: its pet aura (186254) carries the damage bonus and an absorb in one spell",
    107574: "Avatar: a talent puts AoE avoidance and damage reduction on the cast itself",
}


def _side_effect(data: ClientData, spell_id: int, hit: _Hit) -> bool:
    """A defensive hit from a triggered spell too short to be a cooldown (``_MIN_TRIGGERED_DEFENSIVE_MS``)."""
    if hit.role != "defensive" or hit.effect.spell_id == spell_id:
        return False
    aura = data.spells.get(hit.effect.spell_id)
    return aura is not None and 0 < aura.duration_ms < _MIN_TRIGGERED_DEFENSIVE_MS


def classify_defensive(data: ClientData, spell_id: int, *, min_cooldown_s: float = 60.0) -> Candidate | None:
    """A defensive-cooldown candidate for ``spell_id``, or None if it is not a player cooldown that helps the raid
    live."""
    return _classify(data, spell_id, offensive=False, min_cooldown_s=min_cooldown_s)


def classify_offensive(data: ClientData, spell_id: int, *, min_cooldown_s: float = 60.0) -> Candidate | None:
    """An offensive-cooldown candidate for ``spell_id``, or None if it is not a player cooldown that raises damage
    done."""
    return _classify(data, spell_id, offensive=True, min_cooldown_s=min_cooldown_s)


def _duration_ms(data: ClientData, spell: Spell, aura_ids: list[int], same_name: bool) -> float:
    """How long a cooldown's effect lasts: its aura spells' duration, or the cast's own."""
    durations = [data.spells[a].duration_ms for a in aura_ids if a in data.spells and data.spells[a].duration_ms > 0]
    if spell.duration_ms > 0 and (same_name or not durations):
        # Same-name spells include old and talent variants with unrelated durations; the cast's own wins.
        return spell.duration_ms
    if durations:
        return min(durations) if same_name else max(durations)
    return 0


def _classify(data: ClientData, spell_id: int, *, offensive: bool, min_cooldown_s: float) -> Candidate | None:
    spell = data.spells.get(spell_id)
    owner = data.player_spells.get(spell_id)
    if spell is None or owner is None or spell.effective_cooldown_ms < min_cooldown_s * 1000:
        return None
    if not offensive and spell_id in _NOT_DEFENSIVE:
        return None
    roles = {"offensive"} if offensive else _DEFENSIVE_ROLES
    every = list(_hits(data, spell_id))
    linked = _same_name_hits(data, spell)
    # On an offensive cooldown, other auras on the ally (Power Infusion's second aura) and a brief triggered
    # immunity (Metamorphosis's landing) belong to the offensive effect, not to a defensive cooldown.
    has_offensive = any(h.role == "offensive" for h in [*every, *linked])

    def keep(h: _Hit) -> bool:
        side = has_offensive and (h.role == "support" or _side_effect(data, spell_id, h))
        return h.role in roles and not side

    hits = [h for h in every if keep(h)]
    same_name = False
    if not any(h.role != "support" for h in hits):
        fallback = [h for h in linked if keep(h)]
        if fallback:
            hits, same_name = fallback, True
    if not hits:
        return None
    # Defensive and healing effects decide the target; support auras count only when there is nothing else.
    main = [h for h in hits if h.role != "support"] or hits
    target = max((h.target for h in main), key=_TARGET_RANK.__getitem__)
    chosen = [h for h in main if h.target == target]
    role = "defensive" if any(h.role == "defensive" for h in chosen) else chosen[0].role
    aura_ids = list(dict.fromkeys(h.effect.spell_id for h in chosen))
    duration_ms = _duration_ms(data, spell, aura_ids, same_name)
    class_id, specs = owner
    return Candidate(
        spell_id=spell_id,
        name=spell.name,
        class_name=CLASS_NAMES[class_id],
        target=target,
        role=role,
        cooldown_s=spell.effective_cooldown_ms / 1000,
        duration_s=_seconds(duration_ms),
        charges=spell.charges,
        spec_ids=sorted(specs),
        aura_spell_ids=[a for a in aura_ids if a != spell_id],
        evidence=[h.describe(same_name=same_name) for h in main],
    )


def defensive_candidates(data: ClientData, *, min_cooldown_s: float = 60.0) -> list[Candidate]:
    """Every player cooldown in the client data that helps the raid live, by class then name."""
    found = (classify_defensive(data, s, min_cooldown_s=min_cooldown_s) for s in data.player_spells)
    return sorted((x for x in found if x), key=lambda x: (x.class_name, x.name, x.spell_id))


def offensive_candidates(data: ClientData, *, min_cooldown_s: float = 60.0) -> list[Candidate]:
    """Every player cooldown in the client data that raises damage done, by class then name.

    A spell can be a candidate in both lists (an aura that adds damage and cuts damage taken); a person decides
    which file it belongs in, since no spell may be in both.
    """
    found = (classify_offensive(data, s, min_cooldown_s=min_cooldown_s) for s in data.player_spells)
    return sorted((x for x in found if x), key=lambda x: (x.class_name, x.name, x.spell_id))


# ---------------------------------------------------------------------------
# Utility cooldowns
# ---------------------------------------------------------------------------

# Ally-side utility. Speed and mana auras count only with a positive value, cost auras only with a negative one.
SPEED_AURAS = frozenset(
    {
        c.A_MOD_INCREASE_SPEED,
        c.A_MOD_SPEED_ALWAYS,
        c.A_MOD_SPEED_NOT_STACK,
        c.A_MOD_MINIMUM_SPEED_PCT,
        c.A_MOD_MINIMUM_SPEED,
    }
)
_MANA_REGEN_AURAS = {c.A_PERIODIC_ENERGIZE, c.A_MOD_POWER_REGEN_PERCENT}  # misc value is the power type
_MANA_COST_AURAS = {c.A_MOD_POWER_COST_SCHOOL_PCT, c.A_MOD_MANA_COST_PCT}
_ENERGIZE_EFFECTS = {c.E_ENERGIZE, c.E_ENERGIZE_PCT}
_RESURRECT_EFFECTS = {c.E_RESURRECT, c.E_RESURRECT_WITH_AURA}
# Immunity to the mechanics that stop a player moving: Blessing of Freedom's root and snare immunity.
_FREEDOM_MECHANICS = {c.MECHANIC_ROOT, c.MECHANIC_SNARE, c.MECHANIC_SLOW, c.MECHANIC_DAZE, c.MECHANIC_FREEZE}
_FREEDOM_STATES = {c.A_MOD_ROOT, c.A_MOD_ROOT_2, c.A_MOD_DECREASE_SPEED}
# Effects that move a unit: on an ally a reposition (Leap of Faith), on an enemy a grip or a knockback.
_PULL_EFFECTS = {c.E_PLAYER_PULL, c.E_PULL_TOWARDS_DESTIANTION}
_KNOCKBACK_EFFECTS = {c.E_KNOCK_BACK, c.E_KNOCK_BACK_DESTINATION}
_ALLY_MOVE_EFFECTS = {*_PULL_EFFECTS, c.E_JUMP, c.E_JUMP_DEST, c.E_LEAP}

# Enemy-side control, by aura type and, for effects whose aura type doesn't say (scripted or dummy auras), by the
# mechanic the effect carries. A slow counts only with a negative value.
_CONTROL_AURAS: dict[int, str] = {
    c.A_MOD_STUN: "stun",
    c.A_MOD_STUN_DISABLE_GRAVITY: "stun",
    c.A_MOD_CONFUSE: "incapacitate",
    c.A_MOD_FEAR: "incapacitate",
    c.A_MOD_FEAR_2: "incapacitate",
    c.A_MOD_ROOT: "root",
    c.A_MOD_ROOT_2: "root",
    c.A_MOD_ROOT_DISABLE_GRAVITY: "root",
    c.A_MOD_DECREASE_SPEED: "slow",
}
_CONTROL_MECHANICS: dict[int, str] = {
    c.MECHANIC_STUN: "stun",
    c.MECHANIC_DISORIENT: "incapacitate",
    c.MECHANIC_FLEE: "incapacitate",
    c.MECHANIC_SLEEP: "incapacitate",
    c.MECHANIC_INCAPACITATE: "incapacitate",
    c.MECHANIC_POLYMORPH: "incapacitate",
    c.MECHANIC_ROOT: "root",
    c.MECHANIC_FREEZE: "root",
    c.MECHANIC_SNARE: "slow",
    c.MECHANIC_SLOW: "slow",
    c.MECHANIC_DAZE: "slow",
    c.MECHANIC_KNOCKBACK: "knockback",
}

#: Utility kinds, the strongest first: a candidate's ``role`` is the first of its kinds in this order.
UTILITY_KINDS = (
    "resurrect",
    "mana",
    "freedom",
    "reposition",
    "movement",
    "stun",
    "incapacitate",
    "knockback",
    "grip",
    "root",
    "slow",
)
# Enemy targets rank above ally ones, so a cast that controls enemies and also touches the caster (a slow-immune
# leap that roots where it lands) is listed for what it does to enemies.
_UTILITY_TARGET_RANK = {**_TARGET_RANK, "enemy": 3, "enemies": 4}
# Utility casts also reach their effect through a scripted effect on the cast (Leap of Faith, Shockwave).
_UTILITY_INDIRECT = frozenset({*_INDIRECT_EFFECTS, c.E_SCRIPT_EFFECT})


def _enemy_target(effect: Effect) -> tuple[str, str] | None:
    """("enemy" or "enemies", label) for an effect that can land on enemies, or None.

    T_UNIT_TARGET_ANY counts as one enemy here: a stun or a grip on it is cast on a hostile unit.
    """
    if effect.type in c.AREA_AURA_EFFECTS:
        return None
    targets = {effect.target_1, effect.target_2} - {0}
    for t in targets:
        if t in c.AREA_ENEMY_TARGETS:
            return "enemies", c.AREA_ENEMY_TARGETS[t]
    for t in targets:
        if t in c.SINGLE_ENEMY_TARGETS:
            return "enemy", c.SINGLE_ENEMY_TARGETS[t]
        if t == c.T_UNIT_TARGET_ANY:
            return "enemy", "T_UNIT_TARGET_ANY"
    return None


def _ally_utility(effect: Effect) -> tuple[str, str] | None:
    """(kind, what) when an effect helps an ally move, cast or come back, before checking its target."""
    if effect.type in _RESURRECT_EFFECTS:
        return "resurrect", c.EFFECT_TYPES[effect.type]
    if effect.type in _ENERGIZE_EFFECTS and effect.misc_value == c.POWER_MANA and effect.base_value > 0:
        return "mana", c.EFFECT_TYPES[effect.type]
    if effect.type in _ALLY_MOVE_EFFECTS:
        return "reposition", c.EFFECT_TYPES[effect.type]
    if effect.type not in c.AURA_EFFECTS:
        return None
    what = c.AURA_TYPES.get(effect.subtype, f"aura {effect.subtype}")
    if effect.subtype in SPEED_AURAS and effect.base_value > 0:
        return "movement", what
    if effect.subtype in _MANA_REGEN_AURAS and effect.misc_value == c.POWER_MANA and effect.base_value > 0:
        return "mana", what
    if effect.subtype in (c.A_MOD_MANA_REGEN_PCT, c.A_OBS_MOD_MANA) and effect.base_value > 0:
        return "mana", what
    if effect.subtype in _MANA_COST_AURAS and effect.base_value < 0:
        return "mana", what
    if effect.subtype == c.A_MECHANIC_IMMUNITY and effect.misc_value in _FREEDOM_MECHANICS:
        return "freedom", f"{what} {c.MECHANICS[effect.misc_value]}"
    if effect.subtype == c.A_STATE_IMMUNITY and effect.misc_value in _FREEDOM_STATES:
        return "freedom", f"{what} {c.AURA_TYPES[effect.misc_value]}"
    return None


def _enemy_utility(effect: Effect) -> tuple[str, str] | None:
    """(kind, what) when an effect controls or moves a unit, before checking its target."""
    if effect.type in _KNOCKBACK_EFFECTS:
        return "knockback", c.EFFECT_TYPES[effect.type]
    if effect.type in _PULL_EFFECTS:
        return "grip", c.EFFECT_TYPES[effect.type]
    if effect.type in c.AURA_EFFECTS and effect.subtype in _CONTROL_AURAS:
        if effect.subtype == c.A_MOD_DECREASE_SPEED and effect.base_value >= 0:
            return None
        return _CONTROL_AURAS[effect.subtype], c.AURA_TYPES[effect.subtype]
    if effect.mechanic in _CONTROL_MECHANICS:
        return _CONTROL_MECHANICS[effect.mechanic], c.MECHANICS[effect.mechanic]
    return None


def _utility_hit(effect: Effect) -> _Hit | None:
    """How one effect helps allies move, cast or come back, or controls enemies, or None."""
    ally = _ally_utility(effect)
    if ally:
        kind, what = ally
        if kind == "resurrect":
            targets = {effect.target_1, effect.target_2} & set(c.CORPSE_ALLY_TARGETS)
            if targets:
                return _Hit(effect, kind, "external", what, c.CORPSE_ALLY_TARGETS[min(targets)])
        found = _effect_target(effect)
        # A leap or a pull of the caster alone is the caster's own movement, not help for an ally.
        if found and not (kind == "reposition" and found[0] == "personal"):
            return _Hit(effect, kind, found[0], what, found[1])
    enemy = _enemy_utility(effect)
    if enemy:
        found = _enemy_target(effect)
        if found:
            return _Hit(effect, enemy[0], found[0], enemy[1], found[1])
    return None


def classify_utility(data: ClientData, spell_id: int, *, min_cooldown_s: float = 60.0) -> Candidate | None:
    """A utility-cooldown candidate for ``spell_id``, or None.

    Ally-side utility moves players faster, gives mana, brings a dead player back, frees players from roots and
    snares or moves an ally (Stampeding Roar, Innervate, Rebirth, Blessing of Freedom, Leap of Faith). Enemy-side
    utility stuns, incapacitates, roots, slows, grips or knocks back enemies (Capacitor Totem, Gorefiend's Grasp,
    Typhoon). ``target`` is personal, external or raid for allies, enemy or enemies for one enemy or an area, and
    ``role`` is the strongest kind in ``UTILITY_KINDS``.
    """
    spell = data.spells.get(spell_id)
    owner = data.player_spells.get(spell_id)
    if spell is None or owner is None or spell.effective_cooldown_ms < min_cooldown_s * 1000:
        return None
    hits = list(_hits(data, spell_id, hit_fn=_utility_hit))
    same_name = False
    if not hits:
        hits = _same_name_hits(data, spell, _utility_hit, _UTILITY_INDIRECT)
        same_name = bool(hits)
    if not hits:
        return None
    target = max((h.target for h in hits), key=_UTILITY_TARGET_RANK.__getitem__)
    chosen = [h for h in hits if h.target == target]
    # The role is the strongest kind on the target's side, so a single-target fear beside an area slow is
    # listed as an incapacitate on enemies.
    side = [h for h in hits if (h.target in _ALLY_TARGETS) == (target in _ALLY_TARGETS)]
    role = min((h.role for h in side), key=UTILITY_KINDS.index)
    aura_ids = list(dict.fromkeys(h.effect.spell_id for h in chosen if h.effect.type in c.AURA_EFFECTS))
    class_id, specs = owner
    return Candidate(
        spell_id=spell_id,
        name=spell.name,
        class_name=CLASS_NAMES[class_id],
        target=target,
        role=role,
        cooldown_s=spell.effective_cooldown_ms / 1000,
        duration_s=_seconds(_duration_ms(data, spell, aura_ids, same_name)),
        charges=spell.charges,
        spec_ids=sorted(specs),
        aura_spell_ids=[a for a in aura_ids if a != spell_id],
        evidence=[h.describe(same_name=same_name) for h in hits],
    )


def utility_candidates(data: ClientData, *, min_cooldown_s: float = 60.0) -> list[Candidate]:
    """Every player cooldown in the client data with an ally- or enemy-side utility effect, by class then name.

    A spell can be a candidate here and in another list (a damage cooldown that also roots); a person decides
    which file it belongs in.
    """
    found = (classify_utility(data, s, min_cooldown_s=min_cooldown_s) for s in data.player_spells)
    return sorted((x for x in found if x), key=lambda x: (x.class_name, x.name, x.spell_id))


def scripted_spells(data: ClientData, *, min_cooldown_s: float = 60.0) -> list[tuple[int, str, str]]:
    """(spell ID, name, class) of long-cooldown player spells whose effects are scripted, in no candidate list.

    The game implements some cooldowns in server scripts behind dummy effects (Survival Instincts, Healing
    Tide Totem, Vampiric Embrace), so the data can't say what they do. Spells that also deal damage are left
    out; what remains is the list a person reads to catch what the heuristic can't.
    """
    out = []
    for spell_id, (class_id, _) in data.player_spells.items():
        spell = data.spells.get(spell_id)
        if spell is None or spell.effective_cooldown_ms < min_cooldown_s * 1000:
            continue
        own = data.effects.get(spell_id, [])
        scripted = any(
            e.type in _INDIRECT_EFFECTS or (e.type in c.AURA_EFFECTS and e.subtype in _DUMMY_AURAS) for e in own
        )
        if not scripted or any(e.type == c.E_SCHOOL_DAMAGE for e in own):
            continue
        if not _any_candidate(data, spell_id, min_cooldown_s):
            out.append((spell_id, spell.name, CLASS_NAMES[class_id]))
    return sorted(out, key=lambda r: (r[2], r[1], r[0]))


def _any_candidate(data: ClientData, spell_id: int, min_cooldown_s: float) -> bool:
    return any(
        f(data, spell_id, min_cooldown_s=min_cooldown_s)
        for f in (classify_defensive, classify_offensive, classify_utility)
    )


def dropped_effects(data: ClientData, spell_id: int) -> list[int]:
    """Effect indexes below a spell's highest one that SimC's tables leave out."""
    present = {e.index for e in data.effects.get(spell_id, [])}
    return sorted(set(range(max(present))) - present) if present else []


def dropped_effect_spells(data: ClientData, *, min_cooldown_s: float = 60.0) -> list[tuple[int, str, str, list[int]]]:
    """(spell ID, name, class, missing effect indexes) of long-cooldown player spells SimC kept only part of.

    SimC's tables keep the effects its simulations read and drop the rest, which leaves gaps in a spell's effect
    indexes: Rebirth keeps only its dummy effect, not the resurrection, and Blind only its slow, not the
    disorient. Spells in a candidate list are left out; what remains may do something the data can't show.
    """
    out = []
    for spell_id, (class_id, _) in data.player_spells.items():
        spell = data.spells.get(spell_id)
        if spell is None or spell.effective_cooldown_ms < min_cooldown_s * 1000:
            continue
        missing = dropped_effects(data, spell_id)
        if missing and not _any_candidate(data, spell_id, min_cooldown_s):
            out.append((spell_id, spell.name, CLASS_NAMES[class_id], missing))
    return sorted(out, key=lambda r: (r[2], r[1], r[0]))


# ---------------------------------------------------------------------------
# Friendly damage
# ---------------------------------------------------------------------------


@dataclass
class FriendlyDamageCandidate:
    spell_id: int
    name: str
    #: Class names from the spell's class mask; more than one when the mask covers several classes.
    player_classes: list[str]
    cooldown_s: float | None
    duration_s: float | None
    #: Damage effects that land on the caster or allies.
    evidence: list[str] = field(default_factory=list)
    #: The registry's ``origin``: a class mask always means a class spell or talent.
    origin: str = "class"


def _friendly_damage(effect: Effect) -> str | None:
    """``<spell>#<index> <effect> <targets>`` for damage that lands only on the caster or allies, else None."""
    if effect.type in DAMAGE_EFFECTS:
        what = c.EFFECT_TYPES[effect.type]
    elif effect.type in c.AURA_EFFECTS and effect.subtype in DAMAGE_AURAS:
        what = c.AURA_TYPES[effect.subtype]
    else:
        return None
    targets = {effect.target_1, effect.target_2} - {0}
    if effect.type in c.AREA_AURA_EFFECTS:
        where = c.EFFECT_TYPES[effect.type]
    elif targets and targets <= _FRIENDLY_TARGETS:
        where = "+".join(c.TARGETS[t] for t in sorted(targets))
    else:
        return None
    return f"{effect.spell_id}#{effect.index} {what} {where}"


def friendly_damage_candidates(data: ClientData) -> list[FriendlyDamageCandidate]:
    """Every spell with a class mask whose damage lands only on the caster or allies, by class then name.

    Player spells and the spells they trigger alike: the damage is often a separate, unlisted spell
    (Stagger's DoT, Shadow Word: Death's backlash). The data can't say whether the damage is shared out,
    delayed boss damage, a chosen cost or a proc, so that is left to a person.
    """
    out = []
    for spell in data.spells.values():
        if not spell.class_mask:
            continue
        evidence = [e for e in map(_friendly_damage, data.effects.get(spell.spell_id, [])) if e]
        if evidence:
            out.append(
                FriendlyDamageCandidate(
                    spell_id=spell.spell_id,
                    name=spell.name,
                    player_classes=class_names(spell.class_mask),
                    cooldown_s=_seconds(spell.effective_cooldown_ms),
                    duration_s=_seconds(spell.duration_ms),
                    evidence=evidence,
                )
            )
    return sorted(out, key=lambda x: (x.player_classes, x.name, x.spell_id))


# ---------------------------------------------------------------------------
# Registry check
# ---------------------------------------------------------------------------


class _Proposal(Protocol):
    spell_id: int
    cooldown_s: float | None
    duration_s: float | None


@dataclass(frozen=True)
class RegistryCheck:
    spell_id: int
    registry_name: str
    #: matched, name_differs, <field>_differs, not_a_candidate or not_in_game_data
    status: str
    game_name: str | None = None
    #: The candidate's value of the compared field (``target`` for cooldowns, ``origin`` for friendly damage).
    proposed: str | None = None
    cooldown_s: float | None = None
    duration_s: float | None = None


def check_registry(
    data: ClientData, entries: Iterable[tuple[int, str, str | None]], found: Iterable[_Proposal], field: str
) -> list[RegistryCheck]:
    """Compare registry entries, as (spell ID, name, value of ``field``), with the game data and the candidates.

    ``field`` names the attribute both sides carry: ``target`` for cooldowns, ``origin`` for friendly damage
    (a candidate's origin is always ``class``). A mismatch has status ``<field>_differs``.
    """
    by_id = {x.spell_id: x for x in found}
    out = []
    for spell_id, name, value in entries:
        spell = data.spells.get(spell_id)
        if spell is None:
            out.append(RegistryCheck(spell_id, name, "not_in_game_data"))
            continue
        cand = by_id.get(spell_id)
        proposed = getattr(cand, field) if cand else None
        if spell.name != name:
            status = "name_differs"
        elif cand is None:
            status = "not_a_candidate"
        elif proposed != value:
            status = f"{field}_differs"
        else:
            status = "matched"
        out.append(
            RegistryCheck(
                spell_id,
                name,
                status,
                game_name=spell.name,
                proposed=proposed,
                cooldown_s=cand.cooldown_s if cand else _seconds(spell.effective_cooldown_ms),
                duration_s=cand.duration_s if cand else _seconds(spell.duration_ms),
            )
        )
    return out
