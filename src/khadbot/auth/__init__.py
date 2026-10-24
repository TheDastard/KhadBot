"""
auth/__init__.py

Hosted-mode sign-in with Warcraft Logs and report access. ``service.AuthService`` ties the parts together:
``sessions`` (opaque session IDs behind a cookie), ``tokens`` (WCL tokens, Fernet-encrypted at rest, refreshed under a
per-user lock), ``access`` (``can_read``: may this user read this report) and ``routes`` (``/v1/auth/*`` and
``/v1/me``). ``store`` defines what is persisted and keeps it in memory; ``pg`` keeps it in Postgres.

This module imports nothing, so ``auth.errors`` loads without the hosted libraries (``cryptography``, ``psycopg``)
that ``tokens`` and ``pg`` need. Import the module you use directly.
"""

from __future__ import annotations
