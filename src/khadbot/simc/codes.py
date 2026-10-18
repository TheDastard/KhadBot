"""
simc/codes.py

Spell-effect codes from SimC's ``engine/dbc/data_enums.hh``, with the names SimC gives them.

Only the codes KhadBot reads are listed. Each map's values are SimC's enum names, so evidence strings built
from them can be looked up in that header.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Effect types (effect_type_t)
# ---------------------------------------------------------------------------

E_SCHOOL_DAMAGE = 2
E_DUMMY = 3
E_APPLY_AURA = 6
E_HEALTH_LEECH = 9
E_HEAL = 10
E_SUMMON = 28
E_APPLY_AREA_AURA_PARTY = 35
E_TRIGGER_SPELL = 64
E_APPLY_AREA_AURA_RAID = 65
E_HEAL_MAX_HEALTH = 67
E_APPLY_AREA_AURA_FRIEND = 128
E_HEAL_PCT = 136
E_CREATE_AREA_TRIGGER = 179
E_APPLY_AREA_AURA_PARTY_NONRANDOM = 271

EFFECT_TYPES: dict[int, str] = {
    E_SCHOOL_DAMAGE: "E_SCHOOL_DAMAGE",
    E_DUMMY: "E_DUMMY",
    E_APPLY_AURA: "E_APPLY_AURA",
    E_HEALTH_LEECH: "E_HEALTH_LEECH",
    E_HEAL: "E_HEAL",
    E_SUMMON: "E_SUMMON",
    E_APPLY_AREA_AURA_PARTY: "E_APPLY_AREA_AURA_PARTY",
    E_TRIGGER_SPELL: "E_TRIGGER_SPELL",
    E_APPLY_AREA_AURA_RAID: "E_APPLY_AREA_AURA_RAID",
    E_HEAL_MAX_HEALTH: "E_HEAL_MAX_HEALTH",
    E_APPLY_AREA_AURA_FRIEND: "E_APPLY_AREA_AURA_FRIEND",
    E_HEAL_PCT: "E_HEAL_PCT",
    E_CREATE_AREA_TRIGGER: "E_CREATE_AREA_TRIGGER",
    E_APPLY_AREA_AURA_PARTY_NONRANDOM: "E_APPLY_AREA_AURA_PARTY_NONRANDOM",
}

#: Effects that apply an aura to everyone in an area around the caster.
AREA_AURA_EFFECTS = frozenset(
    {E_APPLY_AREA_AURA_PARTY, E_APPLY_AREA_AURA_RAID, E_APPLY_AREA_AURA_FRIEND, E_APPLY_AREA_AURA_PARTY_NONRANDOM}
)
#: Every effect that applies an aura, area or not; its subtype is then an aura type.
AURA_EFFECTS = frozenset({E_APPLY_AURA, *AREA_AURA_EFFECTS})

# ---------------------------------------------------------------------------
# Aura types (effect_subtype_t)
# ---------------------------------------------------------------------------

A_PERIODIC_DAMAGE = 3
A_DUMMY = 4
A_PERIODIC_HEAL = 8
A_PERIODIC_HEAL_PCT = 20
A_PERIODIC_TRIGGER_SPELL = 23
A_MOD_INCREASE_HEALTH = 34
A_SCHOOL_IMMUNITY = 39
A_DAMAGE_IMMUNITY = 40
A_PERIODIC_LEECH = 53
A_SCHOOL_ABSORB = 69
A_SPLIT_DAMAGE_PCT = 81
A_MOD_DAMAGE_PERCENT_TAKEN = 87
A_PERIODIC_DAMAGE_PERCENT = 89
A_MOD_HEALING_RECEIVED_PCT = 118
A_PERIODIC_DUMMY = 226
A_MOD_AOE_DAMAGE_AVOIDANCE = 229
A_MOD_INCREASE_HEALTH_2 = 250
A_SCHOOL_ABSORB_OVERKILL = 316

AURA_TYPES: dict[int, str] = {
    A_PERIODIC_DAMAGE: "A_PERIODIC_DAMAGE",
    A_DUMMY: "A_DUMMY",
    A_PERIODIC_HEAL: "A_PERIODIC_HEAL",
    A_PERIODIC_HEAL_PCT: "A_PERIODIC_HEAL_PCT",
    A_PERIODIC_TRIGGER_SPELL: "A_PERIODIC_TRIGGER_SPELL",
    A_MOD_INCREASE_HEALTH: "A_MOD_INCREASE_HEALTH",
    A_SCHOOL_IMMUNITY: "A_SCHOOL_IMMUNITY",
    A_DAMAGE_IMMUNITY: "A_DAMAGE_IMMUNITY",
    A_PERIODIC_LEECH: "A_PERIODIC_LEECH",
    A_SCHOOL_ABSORB: "A_SCHOOL_ABSORB",
    A_SPLIT_DAMAGE_PCT: "A_SPLIT_DAMAGE_PCT",
    A_MOD_DAMAGE_PERCENT_TAKEN: "A_MOD_DAMAGE_PERCENT_TAKEN",
    A_PERIODIC_DAMAGE_PERCENT: "A_PERIODIC_DAMAGE_PERCENT",
    A_MOD_HEALING_RECEIVED_PCT: "A_MOD_HEALING_RECEIVED_PCT",
    A_PERIODIC_DUMMY: "A_PERIODIC_DUMMY",
    A_MOD_AOE_DAMAGE_AVOIDANCE: "A_MOD_AOE_DAMAGE_AVOIDANCE",
    A_MOD_INCREASE_HEALTH_2: "A_MOD_INCREASE_HEALTH_2",
    A_SCHOOL_ABSORB_OVERKILL: "A_SCHOOL_ABSORB_OVERKILL",
}

# ---------------------------------------------------------------------------
# Targets (effect_targeting)
# ---------------------------------------------------------------------------

T_UNIT_CASTER = 1
T_UNIT_TARGET_ANY = 25

#: One chosen unit. T_UNIT_TARGET_ANY can be an enemy too.
SINGLE_ALLY_TARGETS: dict[int, str] = {
    21: "T_UNIT_TARGET_ALLY",
    T_UNIT_TARGET_ANY: "T_UNIT_TARGET_ANY",
    35: "T_UNIT_TARGET_PARTY",
    57: "T_UNIT_CASTER_TARGET_RAID",
    118: "T_UNIT_TARGET_ALLY_OR_RAID",
}
#: Every friendly unit in an area, a cone or a line.
AREA_ALLY_TARGETS: dict[int, str] = {
    4: "T_UNIT_NEARBY_PARTY",
    20: "T_UNIT_CASTER_AREA_PARTY",
    30: "T_UNIT_SOURCE_AREA_ALLY",
    31: "T_UNIT_DESTINATION_AREA_ALLY",
    33: "T_UNIT_SOURCE_AREA_PARTY",
    34: "T_UNIT_DESTINATION_AREA_PARTY",
    37: "T_UNIT_LAST_TARGET_AREA_PARTY",
    56: "T_UNIT_CASTER_AREA_RAID",
    58: "T_UNIT_CASTER_NEARBY_RAID",
    59: "T_UNIT_CONE_ALLY",
    61: "T_UNIT_TAGET_AREA_RAID_CLASS",
    128: "T_UNIT_RECT_CASTER_ALLY",
    133: "T_UNIT_LINE_CASTER_TO_DESTINATION_ALLY",
    136: "T_UNIT_CONE_CASTER_TO_DESTINATION_ALLY",
}
TARGETS: dict[int, str] = {T_UNIT_CASTER: "T_UNIT_CASTER", **SINGLE_ALLY_TARGETS, **AREA_ALLY_TARGETS}
