"""
tests/integration/hosted/conftest.py

Fixtures for tests that need Postgres. Every test here carries the ``hosted`` marker and is skipped unless
``DATABASE_URL`` is set (the CI ``hosted`` job sets it). Each test gets its own schema, so tests never see each
other's tables and the database can be shared.
"""

from __future__ import annotations

import os
import uuid

import pytest

from khadbot.db import open_pool


def pytest_collection_modifyitems(items):
    for item in items:
        if "tests/integration/hosted" in item.nodeid.replace("\\", "/"):
            item.add_marker(pytest.mark.hosted)


@pytest.fixture(scope="session")
def database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL is not set; the hosted tests need Postgres")
    return url


@pytest.fixture
def schema_name(database_url) -> str:
    """A fresh, empty schema, dropped after the test."""
    name = f"t_{uuid.uuid4().hex[:12]}"
    admin = open_pool(database_url, min_size=1, max_size=1)
    try:
        with admin.connection() as conn:
            conn.execute(f"CREATE SCHEMA {name}")
        yield name
        with admin.connection() as conn:
            conn.execute(f"DROP SCHEMA {name} CASCADE")
    finally:
        admin.close()


@pytest.fixture
def pool(database_url, schema_name):
    """A pool whose connections resolve unqualified names in ``schema_name``."""
    p = open_pool(database_url, min_size=1, max_size=8, kwargs={"options": f"-c search_path={schema_name}"})
    yield p
    p.close()
