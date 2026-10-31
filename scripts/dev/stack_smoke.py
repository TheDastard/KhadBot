"""
scripts/dev/stack_smoke.py

Smoke test for the compose stack, standard library only. With the stack up and seeded, it waits for the API, then
checks the health route, that a signed-out visitor cannot read a seeded report, and the report list and one night
summary as the smoke test user the seed provisions. The user's session comes from `khadbot test-session --user smoke`,
run in the stack's image (the stack's env file turns test sessions on), or with --native-session from main.py on this machine
(the dev loop); --session gives a cookie value instead.

    uv run python scripts/dev/stack_smoke.py [--url http://127.0.0.1:8000] [--timeout 120]

It reads nothing but the API's own routes. The seeded access answers stand for an hour; after that the API asks WCL
(which the stack cannot reach with its throwaway credentials), so seed again. Exit status 0 means every check passed.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

SMOKE_USER = "smoke"  # scripts/data/seed_stack.py provisions this test user
COMPOSE_FILE = Path(__file__).resolve().parents[2] / "deploy" / "compose.yaml"


def get(base: str, path: str, cookie: str | None = None) -> dict:
    """GET ``path`` (with the session ``cookie``, when given) and decode the JSON body; raises on a non-200 status."""
    request = urllib.request.Request(f"{base}{path}", headers={"Cookie": f"khadbot_session={cookie}"} if cookie else {})
    with urllib.request.urlopen(request, timeout=10) as resp:
        return json.load(resp)


def start_session(compose_file: Path, native: bool = False) -> str:
    """
    The cookie value of a new session for the smoke test user (nothing is logged): from the stack's image, or with
    ``native`` from ``main.py`` on this machine using this process's environment (the dev loop's).
    """
    if native:
        main_py = Path(__file__).resolve().parents[2] / "main.py"
        command = [sys.executable, str(main_py), "test-session", "--user", SMOKE_USER, "--quiet"]
    else:
        command = [
            "docker", "compose", "-f", str(compose_file), "run", "--rm", "--no-deps", "-T", "api",
            "test-session", "--user", SMOKE_USER, "--quiet",
        ]  # fmt: skip
    try:
        done = subprocess.run(command, capture_output=True, text=True, timeout=120, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        sys.exit(f"Could not start a test session through docker compose: {exc}")
    lines = [line.strip() for line in done.stdout.splitlines() if line.strip()]
    if done.returncode != 0 or not lines:
        sys.exit(f"`khadbot test-session` failed (exit {done.returncode}): {done.stderr.strip()[-500:]}")
    return lines[-1]


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
    parser.add_argument("--session", help="A session cookie value to use (default: start one for the smoke user)")
    parser.add_argument(
        "--native-session", action="store_true", help="Start the session with main.py here, not in the stack's image"
    )
    parser.add_argument("--compose-file", type=Path, default=COMPOSE_FILE, help="The stack's compose file")
    parser.add_argument("--timeout", type=float, default=120, help="Seconds to wait for the API (default: 120)")
    args = parser.parse_args()
    base = args.url.rstrip("/")

    health = wait_for_health(base, args.timeout)
    if health.get("status") != "ok":
        sys.exit(f"/v1/health is not ok: {health}")
    print(f"health: ok (api {health.get('api_version')})")
    session = args.session or start_session(args.compose_file, args.native_session)

    reports = get(base, "/v1/reports", session)["reports"]
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
    night = get(base, f"/v1/reports/{code}/night", session)
    totals = night["totals"]
    if night["code"] != code or not night["sections"] or not totals["pulls"]:
        sys.exit(f"/v1/reports/{code}/night is empty: {night}")
    print(
        f"night {code}: {totals['pulls']} pulls ({totals['kills']} kills, {totals['wipes']} wipes), "
        f"{len(night['sections'])} encounter sections"
    )


if __name__ == "__main__":
    main()
