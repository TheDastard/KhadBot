"""
blizzard/__init__.py

Blizzard's Game Data API, as far as KhadBot uses it: the client-credentials token and the achievement media lookup
that gives a boss thumbnail's image URL (``docs/game-art.md``, "Boss thumbnails, raid and class images").
"""

from __future__ import annotations

from khadbot.blizzard.client import (
    BlizzardAPIError,
    BlizzardAuthError,
    BlizzardClient,
    BlizzardError,
    BlizzardNotFoundError,
    BlizzardRateLimitedError,
    BlizzardUnavailableError,
)

__all__ = [
    "BlizzardAPIError",
    "BlizzardAuthError",
    "BlizzardClient",
    "BlizzardError",
    "BlizzardNotFoundError",
    "BlizzardRateLimitedError",
    "BlizzardUnavailableError",
]
