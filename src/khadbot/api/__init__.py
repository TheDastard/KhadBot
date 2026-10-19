"""
api/__init__.py

The dashboard API: a read-only FastAPI app over ingested reports (``app``), its endpoints (``routes``), response
models (``schemas``), the night summary (``night``) and the view builders behind them (``views``). The rest of the
code enters through ``create_app`` and ``openapi_document``.
"""

from __future__ import annotations

from khadbot.api.app import create_app, openapi_document

__all__ = ["create_app", "openapi_document"]
