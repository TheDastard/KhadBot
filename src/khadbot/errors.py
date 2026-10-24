"""
errors.py

Exceptions that more than one layer raises or catches. They live at the foundation so engine, services and
entry points can share them without importing each other.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Optional dependency groups
# ---------------------------------------------------------------------------


def missing_group_message(group: str, needed_for: str) -> str:
    """The error text for a feature whose optional libraries are not installed: names the group to install."""
    return (
        f"{needed_for} needs the '{group}' dependency group, which is not installed. "
        f"Install it with: uv sync --extra {group}  (or pip install 'khadbot[{group}]')"
    )


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


class ConfigurationError(Exception):
    """
    Raised when a config file fails schema validation, a declared persona
    file is missing, or a tool name has no Python implementation.
    Surfaced at startup — misconfigurations fail before the first request.
    """

    pass
