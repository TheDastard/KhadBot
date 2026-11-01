"""
api/schemas/common.py

Shared pieces of the API schemas: the API version, the literal types several areas use, and the error body.
"""

from __future__ import annotations

from typing import Literal

from khadbot.errors import ErrorBody as ErrorBody  # defined at the foundation so sign-in shares it

API_VERSION = "1.0.0"

Coverage = Literal["full", "partial", "none"]
Outcome = Literal["kill", "wipe"]
DifficultyName = Literal["lfr", "normal", "heroic", "mythic"]
