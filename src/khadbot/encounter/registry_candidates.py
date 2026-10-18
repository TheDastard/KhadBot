"""
encounter/registry_candidates.py

Propose cooldown-registry and friendly-damage-registry entries from SimC client data (``khadbot.simc``),
and check the registries' entries against that data.

A cooldown candidate is a player spell with a cooldown of at least ``min_cooldown_s`` whose own effects, or
the effects of a spell it triggers, do one of three things:

- defensive: apply less damage taken, an absorb, an immunity, more maximum health, split damage, more
  healing taken or a cheat-death absorb;
- healing: heal an area, or heal one target for a share of its maximum health;
- support: put any other aura on one chosen ally (Innervate, Power Infusion).

Its ``target`` comes from who the effect lands on: only the caster is ``personal``, one chosen ally is
``external``, an area or the raid is ``raid``. Casts that work through an area trigger, a totem or a
scripted effect fall back to same-name spells for their aura. All of this is a heuristic; every
candidate is unverified until a human checks it.

A friendly-damage candidate (for ``config/registries/friendly_damage.yaml``) is any spell with a class
mask, player spell or not, with a damage effect or damage aura that lands only on the caster or allies:
Stagger, Burning Rush, Shadow Word: Death's backlash. The data can't tell shared-out damage from
delayed boss damage or a chosen cost, and it misses damage done in server scripts (Blessing of
Sacrifice, Time Dilation), spells SimC leaves out of its tables (Spirit Link's damage) and item or
borrowed-power effects (class mask 0), so recorded logs stay the main way to find these.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
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
HEALING_AURAS = frozenset({c.A_PERIODIC_HEAL, c.A_PERIODIC_HEAL_PCT})
HEAL_EFFECTS = frozenset({c.E_HEAL, c.E_HEAL_MAX_HEALTH, c.E_HEAL_PCT})
# Heals that count on a single target too; a plain E_HEAL counts only when it lands on an area.
_BIG_HEALS = {c.E_HEAL_MAX_HEALTH, c.E_HEAL_PCT}
# Casts whose aura comes from a separate, unlinked spell (see _same_name_hits).
_INDIRECT_EFFECTS = {c.E_DUMMY, c.E_SUMMON, c.E_CREATE_AREA_TRIGGER}
_AREA_PLACEMENT = {c.E_SUMMON, c.E_CREATE_AREA_TRIGGER}
_DUMMY_AURAS = {c.A_DUMMY, c.A_PERIODIC_DUMMY}

# Damage that can land on a player: direct effects, and auras applied by E_APPLY_AURA or an area aura.
DAMAGE_EFFECTS = frozenset({c.E_SCHOOL_DAMAGE, c.E_HEALTH_LEECH})
DAMAGE_AURAS = frozenset({c.A_PERIODIC_DAMAGE, c.A_PERIODIC_LEECH, c.A_PERIODIC_DAMAGE_PERCENT})

# Single-target labels that can only be a friendly player; any aura cast on one counts as support
# (Innervate, Power Infusion). T_UNIT_TARGET_ANY can be an enemy, so it is left out.
_SUPPORT_TARGETS = {"T_UNIT_TARGET_ALLY", "T_UNIT_CASTER_TARGET_RAID", "T_UNIT_TARGET_ALLY_OR_RAID"}
# Targets that can only be the caster or a friendly player.
_FRIENDLY_TARGETS = set(c.TARGETS) - {c.T_UNIT_TARGET_ANY}

_TARGET_RANK = {"personal": 0, "external": 1, "raid": 2}
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
    #: defensive (damage reduction, absorbs, immunities, cheat death), healing, or support (any other aura
    #: cast on one ally)
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


def _effect_hit(effect: Effect) -> _Hit | None:
    """How one effect helps the raid survive, or None."""
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
        if target == "external" and label in _SUPPORT_TARGETS:
            return _Hit(effect, "support", target, f"aura {effect.subtype}", label)
        return None
    if effect.type in HEAL_EFFECTS and (target == "raid" or effect.type in _BIG_HEALS):
        return _Hit(effect, "healing", target, c.EFFECT_TYPES[effect.type], label)
    return None


def _hits(data: ClientData, spell_id: int, depth: int = 0) -> Iterator[_Hit]:
    """Hits from a spell's effects and from the spells it triggers, ``_TRIGGER_DEPTH`` levels down."""
    for effect in data.effects.get(spell_id, []):
        hit = _effect_hit(effect)
        if hit:
            yield hit
        triggers = effect.type == c.E_TRIGGER_SPELL or (
            effect.type in c.AURA_EFFECTS and effect.subtype == c.A_PERIODIC_TRIGGER_SPELL
        )
        if triggers and effect.trigger_spell_id and effect.trigger_spell_id != spell_id and depth < _TRIGGER_DEPTH:
            yield from _hits(data, effect.trigger_spell_id, depth + 1)


def _same_name_hits(data: ClientData, spell: Spell) -> list[_Hit]:
    """Defensive or healing hits on other spells that share the cast's name.

    Area triggers, totems and scripted dummy effects apply their aura from a separate spell the data does not
    link to the cast (Power Word: Barrier, Anti-Magic Zone, Rallying Cry). That spell usually has the cast's
    name, so a spell whose effects find nothing falls back to same-name spells that are not player spells.
    """
    own = data.effects.get(spell.spell_id, [])
    if not any(e.type in _INDIRECT_EFFECTS for e in own):
        return []
    # A cast that places something on the ground or targets an area helps everyone near it, whatever target the
    # linked aura itself names.
    area = any(e.type in _AREA_PLACEMENT or (_effect_target(e) or ("",))[0] == "raid" for e in own)
    out: list[_Hit] = []
    for other in data.by_name.get(spell.name, []):
        if other == spell.spell_id or other in data.player_spells:
            continue
        for hit in _hits(data, other):
            if hit.role == "support":
                continue
            out.append(_Hit(hit.effect, hit.role, "raid" if area else hit.target, hit.what, hit.label))
    return out


def classify(data: ClientData, spell_id: int, *, min_cooldown_s: float = 60.0) -> Candidate | None:
    """A registry candidate for ``spell_id``, or None if it is not a player cooldown that helps the raid live."""
    spell = data.spells.get(spell_id)
    owner = data.player_spells.get(spell_id)
    if spell is None or owner is None or spell.effective_cooldown_ms < min_cooldown_s * 1000:
        return None
    hits = list(_hits(data, spell_id))
    same_name = False
    if not any(h.role != "support" for h in hits):
        fallback = _same_name_hits(data, spell)
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
    durations = [data.spells[a].duration_ms for a in aura_ids if a in data.spells and data.spells[a].duration_ms > 0]
    if spell.duration_ms > 0 and (same_name or not durations):
        # Same-name spells include old and talent variants with unrelated durations; the cast's own wins.
        duration_ms = spell.duration_ms
    elif durations:
        duration_ms = min(durations) if same_name else max(durations)
    else:
        duration_ms = 0
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
    found = (classify(data, s, min_cooldown_s=min_cooldown_s) for s in data.player_spells)
    return sorted((x for x in found if x), key=lambda x: (x.class_name, x.name, x.spell_id))


def scripted_spells(data: ClientData, *, min_cooldown_s: float = 60.0) -> list[tuple[int, str, str]]:
    """(spell ID, name, class) of long-cooldown player spells whose effects are scripted, not candidates.

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
        if classify(data, spell_id, min_cooldown_s=min_cooldown_s) is None:
            out.append((spell_id, spell.name, CLASS_NAMES[class_id]))
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
