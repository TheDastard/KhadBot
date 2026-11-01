"""
api/raids/

The Raids page's routes (hosted mode): finding a night and its boss pulls before any pull is loaded. ``create_app``
mounts each router with the sign-in routes.

- ``lookup``: a report's boss pulls from WCL's report metadata (``GET /v1/lookup/report``).
- ``servers``: the US and EU server list (``GET /v1/servers``), from ``config/registries/servers.yaml``.
- ``search``: guild and character search (``GET /v1/search/guild``, ``GET /v1/search/character``), with
  ``wcl_request`` (one search request through the budget), ``search_cache`` (identities and listings kept between
  searches) and ``nights`` (the listed nights, built like a lookup and checked for access).
"""

from __future__ import annotations
