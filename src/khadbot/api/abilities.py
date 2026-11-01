"""
api/abilities.py

``GET /v1/reports/{code}/abilities``: the abilities a report names, by game ID, with the name and icon name WCL gives
them (``masterData.abilities``), read from the cached report metadata (``raw/<code>/report.json``). Gated on report
access like every report route; it calls nothing outside this server.

The dashboard shows an ability as a link to Wowhead and lets Wowhead's own tooltip script add the tooltip and icon in
the browser (``docs/game-art.md``, "Spell tooltips and icons"). The server fetches and stores no Wowhead content. This route
only supplies what that link needs and records carry as an ID alone: the spell ID's name, and WCL's icon name for a
place that wants to show one without the script.
"""

from __future__ import annotations

import re
from typing import Any

import orjson
from fastapi import APIRouter, Depends

from khadbot.api.deps import readable_report
from khadbot.api.routes import Ctx
from khadbot.api.schemas import AbilityIcon, ErrorBody, ReportAbilities
from khadbot.api.views import ApiError
from khadbot.storage import InvalidKeyError

_ICON_NAME = re.compile(r"[a-z0-9_-]{1,100}")
_ICON_EXTENSION = re.compile(r"\.(jpg|jpeg|png|gif)$")

router = APIRouter(
    prefix="/v1", responses={422: {"model": ErrorBody, "description": "Invalid path parameter (bad_request)"}}
)


def normalize_icon(raw: object) -> str | None:
    """
    An icon name as the route gives it: lowercase, no file extension (WCL may add ``.jpg``). ``None`` for anything that
    is not a plain name.
    """
    if not isinstance(raw, str):
        return None
    name = _ICON_EXTENSION.sub("", raw.strip().lower())
    return name if _ICON_NAME.fullmatch(name) else None


@router.get(
    "/reports/{code}/abilities",
    response_model=ReportAbilities,
    dependencies=[Depends(readable_report)],
    responses={
        404: {"model": ErrorBody, "description": "No cached metadata for the report, or one the caller cannot read"},
        503: {"model": ErrorBody, "description": "Warcraft Logs could not answer the access check (wcl_unavailable)"},
    },
    tags=["reports"],
)
def get_abilities(code: str, ctx: Ctx) -> ReportAbilities:
    """
    The abilities in the report's cached metadata: game ID, name and WCL's icon name. `icon` is null for metadata cached
    before the report query asked for it (the recorded fixtures).
    """
    meta = None
    if ctx.raw is not None:
        try:
            meta = ctx.raw.report_meta(code)
        except (InvalidKeyError, orjson.JSONDecodeError):
            meta = None
    if not isinstance(meta, dict):
        raise ApiError(404, "report_not_found", f"Report {code} has no cached metadata.")
    raw: list[dict[str, Any]] = (meta.get("masterData") or {}).get("abilities") or []
    abilities: dict[int, AbilityIcon] = {}
    for a in raw:
        try:
            game_id = int(a["gameID"])
        except (KeyError, TypeError, ValueError):
            continue
        abilities[game_id] = AbilityIcon(
            id=game_id, name=a.get("name") or f"ability-{game_id}", icon=normalize_icon(a.get("icon"))
        )
    return ReportAbilities(code=code, abilities=[abilities[i] for i in sorted(abilities)])
