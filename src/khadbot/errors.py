"""
errors.py

Exceptions that more than one layer raises or catches. They live at the foundation so engine, services and
entry points can share them without importing each other.
"""

from __future__ import annotations

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
