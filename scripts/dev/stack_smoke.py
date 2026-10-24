"""
scripts/dev/stack_smoke.py

Smoke test for the compose stack, standard library only. With the stack up and seeded, it waits for the API, then
checks the health route, the report list and one night summary.

    uv run python scripts/dev/stack_smoke.py [--url http://127.0.0.1:8000] [--timeout 120]

It reads nothing but the API's own routes and never calls WCL. Exit status 0 means every check passed.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request


def get(base: str, path: str) -> dict:
    """GET ``path`` and decode the JSON body; raises on a non-200 status."""
    with urllib.request.urlopen(f"{base}{path}", timeout=10) as resp:
        return json.load(resp)


def wait_for_health(base: str, timeout: float) -> dict:
    """Poll /v1/health until it answers or ``timeout`` seconds pass."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            return get(base, "/v1/health")
        except (urllib.error.URLError, ConnectionError, TimeoutError) as exc:
            if time.monotonic() >= deadline:
                sys.exit(f"The API at {base} did not answer within {timeout:.0f}s ({exc}).")
            time.sleep(2)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--url", default="http://127.0.0.1:8000", help="API base URL")
    parser.add_argument("--timeout", type=float, default=120, help="Seconds to wait for the API (default: 120)")
    args = parser.parse_args()
    base = args.url.rstrip("/")

    health = wait_for_health(base, args.timeout)
    if health.get("status") != "ok":
        sys.exit(f"/v1/health is not ok: {health}")
    print(f"health: ok (api {health.get('api_version')})")

    reports = get(base, "/v1/reports")["reports"]
    if not reports:
        sys.exit("/v1/reports is empty: seed the stack first (mise run stack:seed).")
    print(f"reports: {', '.join(r['code'] for r in reports)}")

    code = reports[0]["code"]
    night = get(base, f"/v1/reports/{code}/night")
    totals = night["totals"]
    if night["code"] != code or not night["sections"] or not totals["pulls"]:
        sys.exit(f"/v1/reports/{code}/night is empty: {night}")
    print(
        f"night {code}: {totals['pulls']} pulls ({totals['kills']} kills, {totals['wipes']} wipes), "
        f"{len(night['sections'])} encounter sections"
    )


if __name__ == "__main__":
    main()
