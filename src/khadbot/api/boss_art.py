"""
api/boss_art.py

The boss art registry: ``config/registries/boss_art.yaml``, one entry per raid boss keyed by WCL encounter ID, holding the
references the art routes need (the journal encounter ID, the journal portrait's file name, the Mythic kill achievement
ID) and ``verified``, whether a person confirmed them. ``load_boss_art`` loads and checks the file; a malformed file
stops the caller (``ConfigurationError``), as the server list does. ``portrait_key`` is the one definition of where a
converted portrait lives in the blob store, shared by the upload script and the route that serves it.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from khadbot.errors import ConfigurationError

# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------

DEFAULT_BOSS_ART_PATH = Path(__file__).resolve().parents[3] / "config" / "registries" / "boss_art.yaml"

# Blob store prefix of the converted portraits, one PNG per portrait name. Retention (``jobs/retention.py``) and the
# bucket lifecycle rules (``scripts/data/apply_bucket_lifecycle.py``) touch only ``records/``, ``raw/`` and ``annotated/``.
PORTRAIT_PREFIX = "art/portraits/"

# A portrait name is a file name stem, so it may hold spaces but nothing that makes a path or a blob key.
_PATH_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


class BossArt(BaseModel):
    """One raid boss's art references. ``None`` is unknown or none, never a guess."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    encounter_id: int = Field(gt=0, description="WCL's encounter ID")
    name: str = Field(min_length=1, description="The boss's name as WCL spells it")
    journal_encounter_id: int | None = Field(default=None, gt=0, description="Blizzard Dungeon Journal encounter ID")
    portrait: str | None = Field(
        default=None, description="Journal portrait file name after UI-EJ-BOSS-, without extension"
    )
    kill_achievement_id: int | None = Field(default=None, gt=0, description="The boss's Mythic kill achievement ID")
    verified: bool = Field(description="A person confirmed every non-null value above")

    @field_validator("portrait")
    @classmethod
    def _portrait_is_a_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not value or value != value.strip() or value.startswith(".") or _PATH_CHARS.search(value):
            raise ValueError(f"portrait {value!r} must be a plain file name stem with no path characters")
        return value


class _File(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bosses: list[BossArt]


def portrait_key(portrait: str) -> str:
    """The blob key of a converted portrait: ``art/portraits/<portrait>.png``."""
    return f"{PORTRAIT_PREFIX}{portrait}.png"


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


def load_boss_art(path: Path | str = DEFAULT_BOSS_ART_PATH) -> Mapping[int, BossArt]:
    """
    The registry in file order, keyed by WCL encounter ID (read-only).

    Raises:
        ConfigurationError: The file is missing or not YAML, has no ``bosses`` list, holds an entry that fails the
            model (unknown field, bad ID, a portrait name with a path character) or lists an encounter ID twice.
    """
    path = Path(path)
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigurationError(f"{path}: cannot read the boss art registry: {exc}") from None
    try:
        parsed = _File.model_validate(raw)
    except ValidationError as exc:
        problems = "; ".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors())
        raise ConfigurationError(f"{path}: invalid boss art registry: {problems}") from None
    bosses: dict[int, BossArt] = {}
    for boss in parsed.bosses:
        if boss.encounter_id in bosses:
            raise ConfigurationError(f"{path}: encounter {boss.encounter_id} appears twice")
        bosses[boss.encounter_id] = boss
    return MappingProxyType(bosses)


__all__ = ["DEFAULT_BOSS_ART_PATH", "PORTRAIT_PREFIX", "BossArt", "load_boss_art", "portrait_key"]
