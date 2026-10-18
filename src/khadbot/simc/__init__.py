"""
simc/__init__.py

SimulationCraft's generated game-client data, for any part of KhadBot that needs spells, talents and their
numbers for a game build.

    codes.py        effect, aura and target codes from SimC's data_enums.hh, with their names
    client_data.py  parse the engine/dbc/generated tables into spells, effects and player spells
    fetch.py        download those tables from the SimC repository, pinned to a commit

The encounter registries' proposals built on this data live in ``encounter/registry_candidates.py``.
"""

from __future__ import annotations
