"""
apps/web/e2e/support/review.py

Resets and sets up review state in the dev loop's Postgres (DATABASE_URL), for review.ts.
Usage:
    review.py reset CODE            delete the review jobs, usage rows and findings of CODE's pulls
    review.py fill-quota USER_ID N  give USER_ID N finished review jobs (on pulls that do not exist), as if used today
"""

from __future__ import annotations

import json
import os
import sys
import uuid

import psycopg

action = sys.argv[1]
with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
    if action == "reset":
        code = sys.argv[2]
        conn.execute(
            "DELETE FROM usage WHERE job_id IN (SELECT id FROM jobs WHERE kind = 'review' AND report_code = %s)",
            (code,),
        )
        conn.execute("DELETE FROM jobs WHERE kind = 'review' AND report_code = %s", (code,))
        conn.execute("DELETE FROM findings WHERE pull_key LIKE %s", (f"{code}:%",))
    elif action == "fill-quota":
        user_id, count = sys.argv[2], int(sys.argv[3])
        for n in range(count):
            pull_key = f"QUOTAFILL:{n + 1}"
            spec = {"job": "review_pull", "pull_key": pull_key, "depth": "standard", "model": None}
            conn.execute(
                "INSERT INTO jobs (id, kind, report_code, pull_key, state, requested_by, auth_kind, spec, created_at,"
                " finished_at, updated_at) VALUES (%s, 'review', 'QUOTAFILL', %s, 'done', %s, 'app', %s::jsonb, now(),"
                " now(), now())",
                (uuid.uuid4().hex, pull_key, user_id, json.dumps(spec)),
            )
    elif action == "clear-quota":
        conn.execute("DELETE FROM jobs WHERE kind = 'review' AND report_code = 'QUOTAFILL'")
    else:
        raise SystemExit(f"unknown action {action}")
