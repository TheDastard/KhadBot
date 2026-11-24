"""
api/names.py

Lookups the pull page uses to show what records carry only as IDs:

- ``GET /v1/encounters/{encounter_id}/names?difficulty=``: phase (with a short name), mechanic (with descriptions,
  verified, abilities, icon ability and a plain-words failure summary) and damage-window names from the
  encounter file's view for that difficulty. The digest stays as it is; the client joins on IDs. Encounter files
  are authored config, not report data, so the route needs no report access.
- ``GET /v1/cooldowns``: every cooldown file entry by spell ID (name, the file's category, verified). It depends on no
  report or encounter, so it is served with a day's ``Cache-Control`` and an ETag of the three files' content (304 on
  ``If-None-Match``).
- ``GET /v1/reports/{code}/players``: the ID, name and class of each player actor that took part in at least one fight,
  from the cached report metadata (``raw/<code>/report.json``), so names can take their class colour. Gated on report
  access like every report route.

All three read only files and the blob store; none calls WCL.
"""

from __future__ import annotations

from typing import Annotated, Any, get_args

import orjson
from fastapi import APIRouter, Depends, Query, Request, Response

from khadbot.api.deps import ACCESS_BUDGET_RESPONSE, readable_report
from khadbot.api.routes import Ctx
from khadbot.api.schemas import (
    CooldownList,
    CooldownName,
    EncounterNames,
    ErrorBody,
    MechanicInfo,
    PhaseName,
    Player,
    PlayerClass,
    PlayerList,
)
from khadbot.api.views import ApiError, difficulty_name
from khadbot.encounter.config_files import (
    Blame,
    CooldownRegistry,
    FailureRule,
    Mechanic,
    cooldowns_hash,
    load_encounter_view,
)
from khadbot.storage import InvalidKeyError

_CLASSES: frozenset[str] = frozenset(get_args(PlayerClass))

COOLDOWNS_MAX_AGE_S = 86400  # a browser keeps the list a day, then revalidates it with the ETag

router = APIRouter(
    prefix="/v1", responses={422: {"model": ErrorBody, "description": "Invalid path or query parameter (bad_request)"}}
)


# ---------------------------------------------------------------------------
# Encounter names
# ---------------------------------------------------------------------------


def short_phase_name(name: str) -> str:
    """
    A phase's short name: the text before the first ``": "`` when the name has one ("Stage Three: Ula'tek's
    Ascension" -> "Stage Three"), else the whole name.
    """
    head, sep, _ = name.partition(": ")
    return head if sep and head else name


def _seconds(ms: int | None) -> str:
    return f"{(ms or 0) / 1000:g} s"


def _blame_words(blame: Blame) -> str:
    """Who a ``per_cluster`` failure is charged to (``docs/encounter/failure-rules.md``, "Occurrences")."""
    if blame.by == "nobody":
        return "charged to no one: it is the whole raid's failure"
    if blame.by == "cast_target":
        who = f"charged to the target of the enemy cast before it (in the {_seconds(blame.ms)} up to it)"
    else:
        who = (
            "charged to whoever last held the NPC's aggro "
            f"(its latest swing or taunt in the {_seconds(blame.ms)} before)"
        )
    if blame.reach_yd is not None:
        who += f"; its reach is {blame.reach_yd:g} yd, and the charged player is shown as in or beyond it at the hit"
    return who


def failure_summary(rule: FailureRule | None) -> str | None:
    """
    A failure rule in plain words: what counts as one failure and who it is charged to, one template per rule from
    ``docs/encounter/failure-rules.md`` with the rule's parameters filled in. None without a rule.
    """
    if rule is None:
        return None
    value, ms = rule.value, rule.ms
    match rule.rule:
        case "any_hit":
            return "Every hit is a failure, charged to the player hit."
        case "hits_per_cluster_gt":
            if not value:
                return "A player hit by an occurrence fails once per occurrence, charged to that player."
            times = "once" if value == 1 else f"{value} times"
            return f"A player hit more than {times} by one occurrence fails once, charged to that player."
        case "hit_amount_gt":
            return f"Each hit above {value:,} damage before mitigation is a failure, charged to the player hit."
        case "hit_while_aura":
            return "A hit on a player carrying one of its auras is a failure, charged to the player hit."
        case "hit_without_aura":
            return "A hit on a player carrying none of its auras is a failure, charged to the player hit."
        case "hits_on_non_tank":
            return (
                f"A hit on anyone but the NPC's tank (its latest swing or taunt in the {_seconds(ms)} before) is a "
                "failure, charged to the player hit."
            )
        case "hits_on_non_target":
            return "A hit on anyone but the target of the enemy's cast is a failure, charged to the player hit."
        case "soak_count_lt":
            return f"An occurrence that hits fewer than {value} players is a failure, charged to no one."
        case "per_cluster":
            assert rule.blame is not None  # required on per_cluster (FailureRule)
            return f"Every occurrence is a failure, {_blame_words(rule.blame)}."
        case "aura_applied":
            return "Each application of its aura on a player is a failure, charged to that player."
        case "debuff_duration_gt":
            return f"A debuff kept longer than {_seconds(ms)} is a failure, charged to the player carrying it."
        case "debuff_stacks_gt":
            return f"Stacks above {value} are one failure per occurrence, charged to no one."
        case "aura_removed_by":
            text = (
                "Losing its aura to one of the abilities that break it is a failure, charged to the player who lost it."
            )
            return text + (" A loss at the player's death also counts, charged to no one." if rule.on_death else "")
        case "not_interrupted":
            return "Each enemy cast that is not interrupted is a failure, charged to no one."
    raise AssertionError(f"no failure summary for rule {rule.rule!r}")  # RuleName is exhaustive above


def mechanic_info(m: Mechanic) -> MechanicInfo:
    """The names route's facts about one mechanic beyond its name and description."""
    return MechanicInfo(
        ability_ids=m.ability_ids,
        icon_ability_id=m.icon_id,
        kind=m.kind,
        severity=m.severity,
        failure_summary=failure_summary(m.failure),
    )


@router.get("/encounters/{encounter_id}/names", response_model=EncounterNames, tags=["encounters"])
def get_names(
    encounter_id: int,
    ctx: Ctx,
    difficulty: Annotated[int, Query(description="WCL difficulty code (1 LFR, 3 Normal, 4 Heroic, 5 Mythic)")],
) -> EncounterNames:
    """
    Display names for the encounter's phase, mechanic and damage-window IDs at one difficulty; each phase also has a
    short name for tight spaces, each mechanic that has one its plain-text description, every mechanic whether a
    person has verified it, and its abilities, icon ability, kind, severity and failure rule in plain words
    (`mechanic_info`). An encounter or difficulty without an encounter file answers an empty lookup, not an
    error. Cooldown names are ``GET /v1/cooldowns``.
    """
    enc = load_encounter_view(encounter_id, difficulty, ctx.data.enc_root)
    return EncounterNames(
        encounter_id=encounter_id,
        difficulty=difficulty,
        difficulty_name=difficulty_name(difficulty),
        encounter_name=enc.name if enc else None,
        phases={p.id: PhaseName(name=p.name, short_name=short_phase_name(p.name)) for p in enc.phases} if enc else {},
        mechanics={m.id: m.name for m in enc.mechanics} if enc else {},
        mechanic_descriptions={m.id: m.description for m in enc.mechanics if m.description} if enc else {},
        mechanic_verified={m.id: m.verified for m in enc.mechanics} if enc else {},
        mechanic_info={m.id: mechanic_info(m) for m in enc.mechanics} if enc else {},
        damage_windows={w.id: w.name for w in enc.damage_windows} if enc else {},
    )


# ---------------------------------------------------------------------------
# Cooldowns
# ---------------------------------------------------------------------------


def cooldowns_response(registries: tuple[CooldownRegistry, ...]) -> tuple[bytes, str]:
    """
    The ``GET /v1/cooldowns`` body for the cooldown files, and its ETag: each file's content hash (``cooldowns_hash``,
    the one records are keyed by), so it changes exactly when one of the files does. A spell in two files keeps its
    first entry (defensive, offensive, utility).
    """
    cooldowns: dict[int, CooldownName] = {}
    for registry in registries:
        for entry in registry.cooldowns:
            cooldowns.setdefault(
                entry.spell_id, CooldownName(name=entry.name, category=registry.category, verified=entry.verified)
            )
    body = CooldownList(cooldowns=cooldowns).model_dump_json().encode()
    return body, '"' + "-".join(cooldowns_hash(r) for r in registries) + '"'


@router.get(
    "/cooldowns",
    response_model=CooldownList,
    responses={304: {"description": "Not modified: the list matches the If-None-Match ETag"}},
    tags=["encounters"],
)
def list_cooldowns(request: Request, ctx: Ctx) -> Any:
    """
    Every cooldown file spell by spell ID: its name, the file it is in (``category``) and whether it has been checked
    against the game. Records carry cooldowns, death defensives and window cooldowns as spell IDs; join them here. It
    depends on no report or encounter and changes only when a cooldown file does: cache it for a day and revalidate
    with its ETag.
    """
    registries = ctx.data.cooldown_registries()
    cached: tuple[tuple[CooldownRegistry, ...], bytes, str] | None = getattr(request.app.state, "cooldowns", None)
    if (
        cached is None
        or len(cached[0]) != len(registries)
        or any(a is not b for a, b in zip(cached[0], registries, strict=True))
    ):
        cached = (registries, *cooldowns_response(registries))
        request.app.state.cooldowns = cached
    _, body, etag = cached
    headers = {"ETag": etag, "Cache-Control": f"public, max-age={COOLDOWNS_MAX_AGE_S}"}
    if etag in [t.strip() for t in request.headers.get("if-none-match", "").split(",")]:
        return Response(status_code=304, headers=headers)
    return Response(content=body, media_type="application/json", headers=headers)


# ---------------------------------------------------------------------------
# Players
# ---------------------------------------------------------------------------


@router.get(
    "/reports/{code}/players",
    response_model=PlayerList,
    dependencies=[Depends(readable_report)],
    responses={
        404: {"model": ErrorBody, "description": "No cached metadata for the report, or one the caller cannot read"},
        503: {"model": ErrorBody, "description": "Warcraft Logs could not answer the access check (wcl_unavailable)"},
        **ACCESS_BUDGET_RESPONSE,
    },
    tags=["reports"],
)
def get_players(code: str, ctx: Ctx) -> PlayerList:
    """
    The report's player actors that took part in at least one fight (a fight's ``friendlyPlayers``), with their class
    (null when WCL gives ``Unknown`` or a class not in ``PlayerClass``), by actor ID. Bystanders a big report lists
    among its actors without them ever being in a fight are left out.
    """
    meta = _report_meta(ctx, code)
    actors: list[dict[str, Any]] = (meta.get("masterData") or {}).get("actors") or []
    fought = {i for f in meta.get("fights") or [] if isinstance(f, dict) for i in f.get("friendlyPlayers") or []}
    players = [
        Player(
            id=a["id"],
            name=a.get("name") or f"actor-{a['id']}",
            class_name=a.get("subType") if a.get("subType") in _CLASSES else None,  # type: ignore[arg-type]
        )
        for a in actors
        if a.get("type") == "Player" and isinstance(a.get("id"), int) and a["id"] in fought
    ]
    return PlayerList(code=code, players=sorted(players, key=lambda p: p.id))


def _report_meta(ctx: Ctx, code: str) -> dict[str, Any]:
    """The cached report metadata, or ``report_not_found``."""
    meta = None
    if ctx.raw is not None:
        try:
            meta = ctx.raw.report_meta(code)
        except (InvalidKeyError, orjson.JSONDecodeError):
            meta = None
    if not isinstance(meta, dict):
        raise ApiError(404, "report_not_found", f"Report {code} has no cached metadata.")
    return meta
