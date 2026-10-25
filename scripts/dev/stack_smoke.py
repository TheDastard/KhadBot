"""
scripts/dev/stack_smoke.py

Smoke test for the compose stack, standard library only. With the stack up and seeded, it waits for the API, then
checks the health route, that a signed-out visitor cannot read a seeded report, and the report list and one night
summary as the smoke user the seed provisions (a session cookie from KHADBOT_STACK_SMOKE_SESSION in deploy/stack.env).

    uv run python scripts/dev/stack_smoke.py [--url http://127.0.0.1:8000] [--timeout 120]

It reads nothing but the API's own routes. The seeded access answers stand for an hour; after that the API asks WCL
(which the stack cannot reach with its throwaway credentials), so seed again. Exit status 0 means every check passed.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request

SMOKE_SESSION = "local-stack-smoke-session"  # deploy/stack.env: KHADBOT_STACK_SMOKE_SESSION


def get(base: str, path: str, cookie: str | None = None) -> dict:
    """GET ``path`` (with the session ``cookie``, when given) and decode the JSON body; raises on a non-200 status."""
    request = urllib.request.Request(f"{base}{path}", headers={"Cookie": f"khadbot_session={cookie}"} if cookie else {})
    with urllib.request.urlopen(request, timeout=10) as resp:
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
    parser.add_argument(
        "--session", default=SMOKE_SESSION, help="The smoke user's session cookie value (stack.env's value)"
    )
    parser.add_argument("--timeout", type=float, default=120, help="Seconds to wait for the API (default: 120)")
    args = parser.parse_args()
    base = args.url.rstrip("/")

    health = wait_for_health(base, args.timeout)
    if health.get("status") != "ok":
        sys.exit(f"/v1/health is not ok: {health}")
    print(f"health: ok (api {health.get('api_version')})")

    reports = get(base, "/v1/reports", args.session)["reports"]
    if not reports:
        sys.exit("/v1/reports is empty: seed the stack first (mise run stack:seed).")
    print(f"reports: {', '.join(r['code'] for r in reports)}")

    code = reports[0]["code"]
    try:
        get(base, f"/v1/reports/{code}/night")
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            sys.exit(f"A signed-out visitor got HTTP {exc.code} for a private report; expected 404.")
    else:
        sys.exit(f"A signed-out visitor could read /v1/reports/{code}/night.")
    print("signed out: 404 for a report WCL does not share publicly")
    night = get(base, f"/v1/reports/{code}/night", args.session)
    totals = night["totals"]
    if night["code"] != code or not night["sections"] or not totals["pulls"]:
        sys.exit(f"/v1/reports/{code}/night is empty: {night}")
    print(
        f"night {code}: {totals['pulls']} pulls ({totals['kills']} kills, {totals['wipes']} wipes), "
        f"{len(night['sections'])} encounter sections"
    )


if __name__ == "__main__":
    main()
