"""
encounter/analysis/friendly_damage.py

Friendly damage: damage players took from their own side. A hit on a player is classed by the friendly-damage
registry when its ability is listed there (whatever the row's source), and as ``unclassified`` when a friendly
player or pet dealt it and neither the registry nor the encounter file names the ability. Everything else is
encounter damage, registered or not.

Redistribution, delayed boss and involuntary damage are counted, not charged to anyone. Self-inflicted damage, a
cost the player chose to pay, is reported per player, charged to the player it hit.
"""

from __future__ import annotations

from collections.abc import Mapping

import polars as pl

from khadbot.encounter.annotate import Annotated
from khadbot.encounter.config_files import EncounterFile, FriendlyDamageRegistry
from khadbot.encounter.models import AbilityDamage, Actor, FriendlyDamage, PlayerSelfDamage, UnregisteredAbility

UNCLASSIFIED = "unclassified"
# by_class order in the digest.
CLASS_ORDER = ("redistribution", "delayed_boss", "self_inflicted", "involuntary", UNCLASSIFIED)
PLAYER_CAP = 5
UNCLASSIFIED_CAP = 5
PLAYER_ABILITIES = 3


def friendly_class(enc: EncounterFile | None, registry: FriendlyDamageRegistry) -> pl.Expr:
    """A hit's friendly-damage class (registry class or ``unclassified``); null for encounter damage."""
    classes = {e.ability_id: e.class_name for e in registry.abilities}
    by_registry = (
        pl.col("ability_id").replace_strict(classes, default=None, return_dtype=pl.Utf8)
        if classes
        else pl.lit(None, dtype=pl.Utf8)
    )
    known = sorted(enc.registered_ability_ids()) if enc else []
    return (
        pl.when(by_registry.is_not_null())
        .then(by_registry)
        .when(pl.col("source_friendly") & ~pl.col("ability_id").is_in(known))
        .then(pl.lit(UNCLASSIFIED))
        .otherwise(pl.lit(None, dtype=pl.Utf8))
    )


def classified_hits(a: Annotated, enc: EncounterFile | None, registry: FriendlyDamageRegistry) -> pl.DataFrame:
    """Damage on friendly targets, ``amount`` null-filled, with a ``friendly_class`` column."""
    return a.hits.with_columns(pl.col("amount").fill_null(0), friendly_class(enc, registry).alias("friendly_class"))


def friendly_damage(
    a: Annotated,
    enc: EncounterFile | None,
    registry: FriendlyDamageRegistry,
    actors: list[Actor],
    abilities: Mapping[int, str],
    *,
    player_cap: int = PLAYER_CAP,
    unclassified_cap: int = UNCLASSIFIED_CAP,
) -> FriendlyDamage:
    """Friendly damage in the pull: totals by class, self-inflicted damage per player, unclassified abilities."""
    hits = classified_hits(a, enc, registry)
    total = int(hits["amount"].sum() or 0)
    rows = hits.filter(pl.col("friendly_class").is_not_null())
    if not total or not rows.height:
        return FriendlyDamage()

    sums = dict(rows.group_by("friendly_class").agg(pl.col("amount").sum()).rows())
    by_class = {c: int(sums[c]) for c in CLASS_ORDER if sums.get(c)}

    names = {x.actor_id: x.name for x in actors}
    own = rows.filter(pl.col("friendly_class") == "self_inflicted")
    per_player = (
        own.group_by("target_id")
        .agg(pl.col("amount").sum().alias("damage"))
        .sort(["damage", "target_id"], descending=[True, False])
        .head(player_cap)
        .rows()
    )
    self_inflicted = []
    for target, damage in per_player:
        top = (
            own.filter(pl.col("target_id") == target)
            .group_by("ability_id")
            .agg(pl.col("amount").sum().alias("damage"))
            .sort(["damage", "ability_id"], descending=[True, False])
            .head(PLAYER_ABILITIES)
            .rows()
        )
        self_inflicted.append(
            PlayerSelfDamage(
                name=names.get(target, f"actor-{target}"),
                damage=int(damage),
                abilities=[AbilityDamage(ability_id=i, name=abilities.get(i), damage=int(d)) for i, d in top],
            )
        )

    unclassified = [
        UnregisteredAbility(
            ability_id=ability_id, name=abilities.get(ability_id), damage=int(d), share_pct=round(d / total * 100, 1)
        )
        for ability_id, d in rows.filter(pl.col("friendly_class") == UNCLASSIFIED)
        .group_by("ability_id")
        .agg(pl.col("amount").sum().alias("damage"))
        .sort(["damage", "ability_id"], descending=[True, False])
        .head(unclassified_cap)
        .rows()
    ]
    damage = sum(by_class.values())
    return FriendlyDamage(
        damage=damage,
        share_pct=round(damage / total * 100, 1),
        by_class=by_class,
        self_inflicted=self_inflicted,
        unclassified=unclassified,
    )
