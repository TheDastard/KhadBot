"""
scripts/wcl/probe_wcl_budget.py

Find out whose WCL budget a request made with a user token spends: the user's or the app's.

It signs you in to Warcraft Logs (OAuth authorization code with PKCE; your browser opens, a listener on a
localhost redirect URL catches the code), then:

  1. reads ``rateLimitData`` twice in a row with the app token and twice with your token, to see what a read
     itself costs;
  2. reads it once more with each token (before);
  3. fetches the current specs of one small recorded pull with your token, to /api/v2/user;
  4. reads it again with each token (after) and prints which counter moved and by how much.

This script SPENDS WCL POINTS (about one pull's ingest). It prints an estimate and waits for you to type ``yes``
before signing in and again before the fetch. The access token lives only in memory: it is never printed,
logged or written, and the refresh token is discarded.

Setup: register the redirect URL printed by ``--help`` on your WCL client (https://www.warcraftlogs.com/api/clients/).
Needs WARCRAFTLOGS_CLIENT_ID and WARCRAFTLOGS_CLIENT_SECRET (environment or nearest ``.env``). The client is used
with PKCE and no secret is sent unless you pass ``--with-secret`` (needed if the client is not registered public).

    uv run python scripts/wcl/probe_wcl_budget.py [--port 8976] [--code H7z1fqDXjRTFnbtg --fight 10]
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import http.server
import secrets
import sys
import tempfile
import time
import webbrowser
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
from dotenv import load_dotenv

from khadbot.config import get_config
from khadbot.encounter.ingest.fetch import RawPageCache, fetch_pull, specs_for
from khadbot.encounter.ingest.normalize import parse_report_meta
from khadbot.encounter.ingest.recorded import recorded_fights, recorded_reports
from khadbot.encounter.models import FetchSpec, Pull
from khadbot.wcl.client import RATE_LIMIT_QUERY, TOKEN_URL, WarcraftLogsClient

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_FIXTURE_ROOT = REPO_ROOT / "tests" / "fixtures" / "encounter" / "wcl"
DEFAULT_PORT = 8976
CALLBACK_PATH = "/callback"

# Same host as TOKEN_URL (the client module's constant); the authorization endpoint of WCL's OAuth.
AUTHORIZE_URL = "https://www.warcraftlogs.com/oauth/authorize"

# A counter has to move by more than this beyond the read overhead to count as moved.
MOVED_MARGIN = 0.5


class ProbeError(Exception):
    """A sign-in or measurement step failed. The message never contains a token."""


# ---------------------------------------------------------------------------
# Secret
# ---------------------------------------------------------------------------


class Secret:
    """A string that prints as ``<redacted>``, so it cannot reach output or logs by accident."""

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        self._value = value

    def reveal(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return "<redacted>"

    __str__ = __repr__


# ---------------------------------------------------------------------------
# PKCE sign-in
# ---------------------------------------------------------------------------


def make_pkce() -> tuple[str, str]:
    """A PKCE ``(verifier, S256 challenge)`` pair (RFC 7636)."""
    verifier = secrets.token_urlsafe(64)
    return verifier, challenge_for(verifier)


def challenge_for(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def redirect_uri(port: int) -> str:
    return f"http://localhost:{port}{CALLBACK_PATH}"


def build_authorize_url(client_id: str, redirect: str, challenge: str, state: str) -> str:
    query = urlencode(
        {
            "client_id": client_id,
            "redirect_uri": redirect,
            "response_type": "code",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": state,
        }
    )
    return f"{AUTHORIZE_URL}?{query}"


@dataclass
class Callback:
    code: str | None = None
    error: str | None = None


def parse_callback(target: str, state: str) -> Callback | None:
    """
    Read the redirect request target. Returns None for anything that is not the callback (a favicon request), an
    error ``Callback`` for a denied or mismatched sign-in, else the authorization code.
    """
    parts = urlsplit(target)
    if parts.path != CALLBACK_PATH:
        return None
    query = parse_qs(parts.query)
    if query.get("state", [None])[0] != state:
        return Callback(error="state mismatch")
    if "error" in query:
        return Callback(error=query["error"][0])
    code = query.get("code", [None])[0]
    return Callback(code=code) if code else Callback(error="no code in the redirect")


class _CallbackServer(http.server.HTTPServer):
    state: str = ""
    result: Callback | None = None


class _Handler(http.server.BaseHTTPRequestHandler):
    server: _CallbackServer

    def do_GET(self) -> None:  # noqa: N802 - http.server API
        result = parse_callback(self.path, self.server.state)
        if result is None:
            self.send_response(404)
            self.end_headers()
            return
        self.server.result = result
        body = b"KhadBot probe: signed in. You can close this tab." if result.code else b"Sign-in failed."
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        """The default logs the request line, which carries the authorization code. Log nothing."""


def start_callback_server(port: int, state: str) -> _CallbackServer:
    server = _CallbackServer(("127.0.0.1", port), _Handler)
    server.state = state
    server.timeout = 1.0
    return server


def wait_for_callback(server: _CallbackServer, timeout_s: float) -> Callback:
    """Serve requests until the callback arrives or ``timeout_s`` passes. Closes the server."""
    deadline = time.monotonic() + timeout_s
    try:
        while server.result is None and time.monotonic() < deadline:
            server.handle_request()
    finally:
        server.server_close()
    return server.result or Callback(error="timed out waiting for the redirect")


async def exchange_code(
    http_client: httpx.AsyncClient,
    *,
    client_id: str,
    code: str,
    verifier: str,
    redirect: str,
    client_secret: str | None = None,
) -> Secret:
    """Trade the authorization code for an access token. Only the status and WCL's error code are reported."""
    data = {
        "grant_type": "authorization_code",
        "client_id": client_id,
        "code": code,
        "code_verifier": verifier,
        "redirect_uri": redirect,
    }
    if client_secret:
        data["client_secret"] = client_secret
    try:
        resp = await http_client.post(TOKEN_URL, data=data)
    except httpx.RequestError as exc:
        raise ProbeError(f"token request failed: {type(exc).__name__}") from None
    if resp.status_code != 200:
        try:
            reason = resp.json().get("error", "unknown")
        except ValueError:
            reason = "unknown"
        raise ProbeError(f"token exchange refused: HTTP {resp.status_code}, error={reason}")
    token = resp.json().get("access_token")
    if not token:
        raise ProbeError("token exchange returned no access_token")
    return Secret(token)


async def sign_in(
    *,
    client_id: str,
    port: int,
    client_secret: str | None,
    timeout_s: float,
    open_browser: Callable[[str], Any] = webbrowser.open,
    out: Callable[[str], None] = print,
) -> Secret:
    """Run the browser sign-in and return the user access token."""
    verifier, challenge = make_pkce()
    state = secrets.token_urlsafe(16)
    redirect = redirect_uri(port)
    server = start_callback_server(port, state)
    url = build_authorize_url(client_id, redirect, challenge, state)
    out(f"Opening your browser to sign in. If it does not open, visit:\n  {url}")
    open_browser(url)
    callback = await asyncio.to_thread(wait_for_callback, server, timeout_s)
    if callback.code is None:
        raise ProbeError(f"sign-in failed: {callback.error}")
    async with httpx.AsyncClient(timeout=30.0) as http_client:
        return await exchange_code(
            http_client, client_id=client_id, code=callback.code, verifier=verifier, redirect=redirect,
            client_secret=client_secret,
        )  # fmt: skip


# ---------------------------------------------------------------------------
# Measuring
# ---------------------------------------------------------------------------


class QueryClient(Protocol):
    async def query(self, gql: str, variables: dict[str, Any] | None = None, **kwargs: Any) -> dict[str, Any]: ...


class UserTokenClient:
    """
    Wraps a client so every query goes out with the user token (to /api/v2/user).

    The ingest's fetcher calls ``client.query(gql, variables)`` and has no token parameter yet; this adds it.
    """

    def __init__(self, inner: QueryClient, token: Secret) -> None:
        self._inner = inner
        self._token = token

    async def query(self, gql: str, variables: dict[str, Any] | None = None, **_: Any) -> dict[str, Any]:
        return await self._inner.query(gql, variables, user_token=self._token.reveal())


@dataclass(frozen=True)
class Reading:
    spent: float
    limit: float
    reset_in: float


async def read_limit(client: QueryClient, token: Secret | None) -> Reading:
    """``rateLimitData`` as seen with the app token (``token`` None) or the user token."""
    kwargs: dict[str, Any] = {"user_token": token.reveal()} if token else {}
    data = (await client.query(RATE_LIMIT_QUERY, None, **kwargs)).get("rateLimitData") or {}
    try:
        return Reading(float(data["pointsSpentThisHour"]), float(data["limitPerHour"]), float(data["pointsResetIn"]))
    except (KeyError, TypeError, ValueError):
        raise ProbeError("rateLimitData came back without the expected fields") from None


@dataclass
class Measurement:
    app_before: Reading
    app_after: Reading
    user_before: Reading
    user_after: Reading
    app_read_cost: float
    user_read_cost: float

    @property
    def app_delta(self) -> float:
        return self.app_after.spent - self.app_before.spent

    @property
    def user_delta(self) -> float:
        return self.user_after.spent - self.user_before.spent


def verdict(m: Measurement) -> str:
    """Which budget the fetch spent. One read's cost, measured beforehand, is netted off each counter's delta."""
    app_net = m.app_delta - m.app_read_cost
    user_net = m.user_delta - m.user_read_cost
    app_moved, user_moved = app_net > MOVED_MARGIN, user_net > MOVED_MARGIN
    if user_moved and app_moved:
        if abs(app_net - user_net) <= 1.0:
            return "BOTH counters moved by the same amount: the user token and the app token appear to share one pool."
        return "BOTH counters moved, by different amounts: inconclusive; something else may have used the app token."
    if user_moved:
        return "USER budget: only the user token's counter moved."
    if app_moved:
        return "APP budget: only the app token's counter moved."
    return "NEITHER counter moved beyond the read cost: inconclusive (the fetch may have been fully cached or free)."


def estimate_lines(n_requests: int, pull_label: str) -> list[str]:
    return [
        f"Fetch of {pull_label}: {n_requests} requests (the current specs), one page each unless the pull is long.",
        "  Documented: about 14 points per pull on average (docs/wcl-api.md, 2026-10-11); about 2 points per request",
        "  plus about 1 per extra page. This is a short pull, so expect roughly 10 to 20 points.",
        "  Plus 8 rateLimitData reads whose own cost is unknown (the first four measure it).",
        "  Sign-in itself spends no points.",
    ]


def choose_small_pull(root: Path) -> tuple[str, int]:
    """The shortest recorded boss pull: its metadata is in ``report.json`` and its pages are recorded."""
    best: tuple[int, str, int] | None = None
    for code in recorded_reports(root):
        meta = RawPageCache(root).report_meta(code)
        if meta is None:
            continue
        pulls, _ = parse_report_meta(code, meta)
        recorded = set(recorded_fights(root, code))
        for pull in pulls:
            if pull.fight_id in recorded and (best is None or pull.duration_ms < best[0]):
                best = (pull.duration_ms, code, pull.fight_id)
    if best is None:
        raise ProbeError(f"no recorded pull under {root} (git lfs pull?)")
    return best[1], best[2]


async def run_probe(
    client: QueryClient,
    token: Secret,
    code: str,
    pull: Pull,
    specs: list[FetchSpec],
    *,
    confirm: Callable[[str], bool],
    out: Callable[[str], None] = print,
) -> Measurement | None:
    """Calibrate the read cost, fetch ``pull`` with the user token, and measure both counters."""
    app_a = await read_limit(client, None)
    app_b = await read_limit(client, None)
    user_a = await read_limit(client, token)
    user_b = await read_limit(client, token)
    app_cost, user_cost = app_b.spent - app_a.spent, user_b.spent - user_a.spent
    out(f"Read cost: app token {app_cost:g} points, user token {user_cost:g} points per read.")

    out("\n" + "\n".join(estimate_lines(len(specs), pull.pull_key)))
    if not confirm("Fetch with the user token now?"):
        out("Stopped before the fetch.")
        return None

    app_before = await read_limit(client, None)
    user_before = await read_limit(client, token)
    with tempfile.TemporaryDirectory(prefix="khadbot-budget-") as tmp:
        await fetch_pull(UserTokenClient(client, token), RawPageCache(Path(tmp)), code, pull, specs)  # type: ignore[arg-type]
    app_after = await read_limit(client, None)
    user_after = await read_limit(client, token)
    return Measurement(app_before, app_after, user_before, user_after, app_cost, user_cost)


def render_report(m: Measurement, code: str, fight: int, today: date | None = None) -> str:
    """The markdown block to paste into docs/wcl-api.md. Contains no token."""
    return "\n".join(
        [
            f"### Budget probe ({(today or date.today()).isoformat()}, `{code}:{fight}`, fetched with a user token)",
            "",
            "| Counter read with | Limit per hour | Spent before | Spent after | Moved | Cost of one read |",
            "|---|---|---|---|---|---|",
            f"| app token | {m.app_before.limit:g} | {m.app_before.spent:g} | {m.app_after.spent:g} | "
            f"{m.app_delta:g} | {m.app_read_cost:g} |",
            f"| user token | {m.user_before.limit:g} | {m.user_before.spent:g} | {m.user_after.spent:g} | "
            f"{m.user_delta:g} | {m.user_read_cost:g} |",
            "",
            f"Verdict: {verdict(m)}",
        ]
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def ask(question: str) -> bool:
    """Wait for a typed ``yes``. No input available (a closed pipe) counts as no."""
    try:
        answer = input(f"{question} Type 'yes' to run: ")
    except EOFError:
        return False
    return answer.strip().lower() in ("y", "yes")


async def _amain(args: argparse.Namespace) -> int:
    from khadbot.jobs import default_filter_kwargs

    code, fight = (args.code, args.fight) if args.code and args.fight else choose_small_pull(args.fixture_root)
    meta = RawPageCache(args.fixture_root).report_meta(code)
    if meta is None:
        raise ProbeError(f"no report.json for {code} under {args.fixture_root}")
    pulls, _ = parse_report_meta(code, meta)
    pull = next((p for p in pulls if p.fight_id == fight), None)
    if pull is None:
        raise ProbeError(f"fight {fight} is not in {code}'s report metadata")
    kwargs = default_filter_kwargs()
    specs = specs_for(
        kwargs["defensive_spell_ids"],
        (kwargs["enemy_cast_ids"] or {}).get(pull.encounter_id),
        kwargs["offensive_spell_ids"],
    )

    wcl = get_config().warcraftlogs
    print(f"Redirect URL this script listens on (register it on your WCL client): {redirect_uri(args.port)}")
    print(f"Pull to fetch: {pull.pull_key}, {pull.duration_ms / 1000:.0f} s.")
    print("\n".join(estimate_lines(len(specs), pull.pull_key)))
    if not ask("Sign in and continue?"):
        print("Stopped before signing in.")
        return 1

    token = await sign_in(
        client_id=wcl.client_id,
        port=args.port,
        client_secret=wcl.client_secret if args.with_secret else None,
        timeout_s=args.timeout,
    )
    print("Signed in (token held in memory only).")
    async with WarcraftLogsClient(wcl.client_id, wcl.client_secret) as client:
        measurement = await run_probe(client, token, code, pull, specs, confirm=ask)
    if measurement is None:
        return 1
    print("\n" + render_report(measurement, code, fight))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.epilog = (
        f"Redirect URL to register on the WCL client: {redirect_uri(DEFAULT_PORT)} (with the default --port)."
    )
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Localhost port of the redirect URL")
    parser.add_argument("--code", help="Recorded report code (default: the shortest recorded pull)")
    parser.add_argument("--fight", type=int, help="Fight ID in that report")
    parser.add_argument("--fixture-root", type=Path, default=DEFAULT_FIXTURE_ROOT)
    parser.add_argument("--timeout", type=float, default=180.0, help="Seconds to wait for the browser redirect")
    parser.add_argument("--with-secret", action="store_true", help="Send the client secret in the token exchange")
    args = parser.parse_args(argv)
    load_dotenv()
    try:
        return asyncio.run(_amain(args))
    except ProbeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
