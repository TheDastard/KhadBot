"""
apps/web/e2e/support/watch.py

Adds or removes a live watch row for a test user in the dev loop's Postgres (DATABASE_URL), for watch.ts.
Usage: watch.py add|remove CODE USER_ID
"""

from __future__ import annotations

import os
import sys
import uuid

import psycopg

action, code, user_id = sys.argv[1:4]
with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
    conn.execute("DELETE FROM watches WHERE report_code = %s", (code,))
    if action == "add":
        conn.execute(
            "INSERT INTO watches (id, report_code, user_id, started_at, expires_at, quiet_since, updated_at) "
            "VALUES (%s, %s, %s, now(), now() + interval '1 hour', now(), now())",
            (uuid.uuid4().hex, code, user_id),
        )
