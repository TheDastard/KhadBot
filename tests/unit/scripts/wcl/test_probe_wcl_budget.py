"""
tests/unit/scripts/test_probe_wcl_budget.py

scripts/wcl/probe_wcl_budget.py: PKCE sign-in pieces, the redirect listener, token handling (a token must never reach
output), the budget verdict and the whole probe against a stub client. No call leaves this machine.
"""

from __future__ import annotations

import json
import threading
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
import respx
from helpers.scripts import load_script

from khadbot.encounter.ingest.fetch import specs_for
from khadbot.encounter.models import Pull
from khadbot.wcl.client import RATE_LIMIT_QUERY, TOKEN_URL

pb = load_script("wcl", "probe_wcl_budget")

TOKEN = "ACCESS-TOKEN-VALUE-1234"
CODE = "AUTH-CODE-VALUE-5678"
VERIFIER = "VERIFIER-VALUE-9012"
SECRET = "CLIENT-SECRET-VALUE-3456"


def make_pull() -> Pull:
    return Pull(
        report_code="ABC123", fight_id=3, encounter_id=7, encounter_name="Boss", difficulty=5, kill=False,
        start_ms=1_000, end_ms=41_000, duration_ms=40_000, last_phase=1, boss_pct=50.0, friendly_player_ids=[1],
    )  # fmt: skip


# ---------------------------------------------------------------------------
# Secret and PKCE
# ---------------------------------------------------------------------------


def test_secret_never_prints():
    s = pb.Secret(TOKEN)
    assert TOKEN not in repr(s) and TOKEN not in str(s) and TOKEN not in f"{s}" and TOKEN not in f"{[s]}"
    assert s.reveal() == TOKEN


def test_pkce_challenge_matches_rfc_7636_example():
    # RFC 7636 appendix B
    assert pb.challenge_for("dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk") == (
        "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"
    )


def test_make_pkce_verifier_length_and_pairing():
    verifier, challenge = pb.make_pkce()
    assert 43 <= len(verifier) <= 128
    assert pb.challenge_for(verifier) == challenge
    assert pb.make_pkce()[0] != verifier


def test_authorize_url_carries_pkce_and_state_not_secrets():
    url = pb.build_authorize_url("cid", "http://localhost:8976/callback", "CHAL", "STATE")
    parts = urlsplit(url)
    assert f"{parts.scheme}://{parts.netloc}{parts.path}" == pb.AUTHORIZE_URL
    q = {k: v[0] for k, v in parse_qs(parts.query).items()}
    assert q == {
        "client_id": "cid",
        "redirect_uri": "http://localhost:8976/callback",
        "response_type": "code",
        "code_challenge": "CHAL",
        "code_challenge_method": "S256",
        "state": "STATE",
    }


def test_redirect_uri_uses_localhost_and_port():
    assert pb.redirect_uri(8976) == "http://localhost:8976/callback"


# ---------------------------------------------------------------------------
# Redirect listener
# ---------------------------------------------------------------------------


def test_parse_callback_cases():
    assert pb.parse_callback("/favicon.ico", "S") is None
    assert pb.parse_callback("/callback?code=abc&state=S", "S").code == "abc"
    assert pb.parse_callback("/callback?code=abc&state=OTHER", "S").error == "state mismatch"
    assert pb.parse_callback("/callback?error=access_denied&state=S", "S").error == "access_denied"
    assert pb.parse_callback("/callback?state=S", "S").error == "no code in the redirect"


def test_listener_receives_the_redirect_and_logs_nothing(capsys):
    server = pb.start_callback_server(0, "S")
    port = server.server_address[1]

    def browser():
        httpx.get(f"http://127.0.0.1:{port}/favicon.ico")
        httpx.get(f"http://127.0.0.1:{port}/callback?code={CODE}&state=S")

    thread = threading.Thread(target=browser)
    thread.start()
    result = pb.wait_for_callback(server, timeout_s=10)
    thread.join()
    assert result.code == CODE
    captured = capsys.readouterr()
    assert CODE not in captured.out + captured.err  # the default handler would log the request line


def test_listener_times_out():
    server = pb.start_callback_server(0, "S")
    result = pb.wait_for_callback(server, timeout_s=0.1)
    assert result.code is None and "timed out" in result.error


# ---------------------------------------------------------------------------
# Token exchange
# ---------------------------------------------------------------------------


@respx.mock
async def test_exchange_code_returns_secret_and_sends_pkce_fields():
    route = respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(200, json={"access_token": TOKEN, "refresh_token": "R", "expires_in": 3600})
    )
    async with httpx.AsyncClient() as http:
        token = await pb.exchange_code(
            http, client_id="cid", code=CODE, verifier=VERIFIER, redirect="http://localhost:8976/callback"
        )
    assert token.reveal() == TOKEN and TOKEN not in repr(token)
    sent = parse_qs(route.calls.last.request.content.decode())
    assert sent["grant_type"] == ["authorization_code"] and sent["code_verifier"] == [VERIFIER]
    assert "client_secret" not in sent


@respx.mock
async def test_exchange_code_sends_secret_only_when_given():
    route = respx.post(TOKEN_URL).mock(return_value=httpx.Response(200, json={"access_token": TOKEN}))
    async with httpx.AsyncClient() as http:
        await pb.exchange_code(http, client_id="cid", code=CODE, verifier=VERIFIER, redirect="r", client_secret=SECRET)
    assert parse_qs(route.calls.last.request.content.decode())["client_secret"] == [SECRET]


@respx.mock
async def test_exchange_code_failure_reports_status_and_error_only():
    respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(400, json={"error": "invalid_grant", "echo": f"{CODE} {VERIFIER} {SECRET}"})
    )
    async with httpx.AsyncClient() as http:
        with pytest.raises(pb.ProbeError) as exc:
            await pb.exchange_code(
                http, client_id="cid", code=CODE, verifier=VERIFIER, redirect="r", client_secret=SECRET
            )
    message = str(exc.value)
    assert "400" in message and "invalid_grant" in message
    assert CODE not in message and VERIFIER not in message and SECRET not in message


@respx.mock
async def test_exchange_code_without_token_or_network_fails_cleanly():
    respx.post(TOKEN_URL).mock(return_value=httpx.Response(200, json={}))
    async with httpx.AsyncClient() as http:
        with pytest.raises(pb.ProbeError, match="no access_token"):
            await pb.exchange_code(http, client_id="c", code=CODE, verifier=VERIFIER, redirect="r")
    respx.post(TOKEN_URL).mock(side_effect=httpx.ConnectError(f"boom {CODE}"))
    async with httpx.AsyncClient() as http:
        with pytest.raises(pb.ProbeError) as exc:
            await pb.exchange_code(http, client_id="c", code=CODE, verifier=VERIFIER, redirect="r")
    assert CODE not in str(exc.value)


async def test_sign_in_end_to_end_with_a_fake_browser():
    opened: list[str] = []

    def fake_browser(url: str) -> None:
        opened.append(url)
        query = parse_qs(urlsplit(url).query)
        port = urlsplit(query["redirect_uri"][0]).port
        state = query["state"][0]
        threading.Thread(
            target=lambda: httpx.get(f"http://127.0.0.1:{port}/callback?code={CODE}&state={state}")
        ).start()

    # port 0 cannot be registered, so reserve a free port first
    probe = pb.start_callback_server(0, "x")
    port = probe.server_address[1]
    probe.server_close()
    out: list[str] = []
    with respx.mock(assert_all_called=False) as mock:
        route = mock.post(TOKEN_URL).mock(return_value=httpx.Response(200, json={"access_token": TOKEN}))
        mock.route(host="127.0.0.1").pass_through()
        token = await pb.sign_in(
            client_id="cid", port=port, client_secret=None, timeout_s=10, open_browser=fake_browser, out=out.append
        )
    assert token.reveal() == TOKEN and route.called
    assert opened and TOKEN not in "\n".join(out)
    challenge = parse_qs(urlsplit(opened[0]).query)["code_challenge"][0]
    verifier = parse_qs(route.calls.last.request.content.decode())["code_verifier"][0]
    assert pb.challenge_for(verifier) == challenge


async def test_sign_in_denied_raises():
    def fake_browser(url: str) -> None:
        query = parse_qs(urlsplit(url).query)
        port = urlsplit(query["redirect_uri"][0]).port
        state = query["state"][0]
        threading.Thread(
            target=lambda: httpx.get(f"http://127.0.0.1:{port}/callback?error=access_denied&state={state}")
        ).start()

    probe = pb.start_callback_server(0, "x")
    port = probe.server_address[1]
    probe.server_close()
    with pytest.raises(pb.ProbeError, match="access_denied"):
        await pb.sign_in(
            client_id="cid", port=port, client_secret=None, timeout_s=10, open_browser=fake_browser, out=lambda s: None
        )


# ---------------------------------------------------------------------------
# Measurement
# ---------------------------------------------------------------------------


class StubBudget:
    """
    A WCL stand-in with two counters. ``charges`` says which counter an events request moves with a user token
    ("user", "app", "both"); an app-token request always moves the app counter.
    """

    def __init__(self, charges: str, read_cost: float = 0.0, per_request: float = 2.0) -> None:
        self.charges = charges
        self.read_cost = read_cost
        self.per_request = per_request
        self.app = 100.0
        self.user = 40.0
        self.event_calls: list[tuple[str | None, dict]] = []

    async def query(self, gql, variables=None, *, user_token=None):
        counter_is_user = user_token is not None
        if gql == RATE_LIMIT_QUERY:
            if counter_is_user:
                self.user += self.read_cost
            else:
                self.app += self.read_cost
            spent = self.user if counter_is_user else self.app
            return {"rateLimitData": {"limitPerHour": 18000 if counter_is_user else 3600,
                                      "pointsSpentThisHour": spent, "pointsResetIn": 1200}}  # fmt: skip
        self.event_calls.append((user_token, variables))
        if not counter_is_user or self.charges in ("app", "both"):
            self.app += self.per_request
        if counter_is_user and self.charges in ("user", "both"):
            self.user += self.per_request
        return {"reportData": {"report": {"events": {"data": [], "nextPageTimestamp": None}}}}


def test_verdict_cases():
    def measurement(app_delta, user_delta, app_cost=0.0, user_cost=0.0):
        r = lambda spent: pb.Reading(spent, 3600, 100)  # noqa: E731
        return pb.Measurement(r(10), r(10 + app_delta), r(5), r(5 + user_delta), app_cost, user_cost)

    assert pb.verdict(measurement(0, 20)).startswith("USER")
    assert pb.verdict(measurement(20, 0)).startswith("APP")
    assert pb.verdict(measurement(20, 20)).startswith("BOTH counters moved by the same")
    assert "different amounts" in pb.verdict(measurement(20, 5))
    assert pb.verdict(measurement(0, 0)).startswith("NEITHER")
    # a counter that only moved by what its own reads cost did not move
    assert pb.verdict(measurement(2, 22, app_cost=2, user_cost=2)).startswith("USER")


@pytest.mark.parametrize(("charges", "expected"), [("user", "USER"), ("app", "APP"), ("both", "BOTH")])
async def test_run_probe_finds_the_charged_budget(charges, expected):
    stub = StubBudget(charges)
    token = pb.Secret(TOKEN)
    specs = specs_for()
    out: list[str] = []
    m = await pb.run_probe(stub, token, "ABC123", make_pull(), specs, confirm=lambda q: True, out=out.append)
    assert m is not None
    assert pb.verdict(m).startswith(expected)
    # every events request carried the user token
    assert stub.event_calls and all(tok == TOKEN for tok, _ in stub.event_calls)
    assert len(stub.event_calls) == len(specs)
    text = "\n".join(out) + pb.render_report(m, "ABC123", 3)
    assert TOKEN not in text


async def test_run_probe_nets_off_read_cost():
    stub = StubBudget("user", read_cost=1.0)
    m = await pb.run_probe(stub, pb.Secret(TOKEN), "ABC123", make_pull(), specs_for(), confirm=lambda q: True,
                           out=lambda s: None)  # fmt: skip
    assert m is not None and m.app_read_cost == 1.0 and m.user_read_cost == 1.0
    assert pb.verdict(m).startswith("USER")


async def test_run_probe_declined_fetches_nothing():
    stub = StubBudget("user")
    out: list[str] = []
    m = await pb.run_probe(stub, pb.Secret(TOKEN), "ABC123", make_pull(), specs_for(), confirm=lambda q: False,
                           out=out.append)  # fmt: skip
    assert m is None and stub.event_calls == []
    assert any("estimate" in line.lower() or "Documented" in line for line in out)


async def test_read_limit_rejects_malformed_data():
    class Bad:
        async def query(self, gql, variables=None, **kw):
            return {"rateLimitData": {"limitPerHour": 1}}

    with pytest.raises(pb.ProbeError):
        await pb.read_limit(Bad(), None)


async def test_user_token_client_forwards_the_token():
    seen = {}

    class Inner:
        async def query(self, gql, variables=None, **kw):
            seen.update(kw)
            return {}

    await pb.UserTokenClient(Inner(), pb.Secret(TOKEN)).query("q", {"a": 1})
    assert seen == {"user_token": TOKEN}


def test_report_has_no_token_and_states_counters():
    r = lambda spent, limit: pb.Reading(spent, limit, 100)  # noqa: E731
    m = pb.Measurement(r(10, 3600), r(10, 3600), r(5, 18000), r(25, 18000), 0.0, 0.0)
    text = pb.render_report(m, "ABC123", 3)
    assert "| app token | 3600 |" in text and "| user token | 18000 |" in text and "USER" in text


def test_estimate_states_its_sources():
    text = "\n".join(pb.estimate_lines(11, "ABC123:3"))
    assert "docs/wcl-api.md" in text and "14 points" in text and "no points" in text


# ---------------------------------------------------------------------------
# Choosing the pull
# ---------------------------------------------------------------------------


def _write_report(root, code, fights, recorded):
    d = root / code
    d.mkdir(parents=True)
    meta = {
        "masterData": {"actors": []},
        "fights": [
            {"id": fid, "encounterID": 1, "name": "B", "difficulty": 5, "kill": False, "startTime": 0, "endTime": ms}
            for fid, ms in fights
        ],
    }
    (d / "report.json").write_text(json.dumps(meta))
    for fid in recorded:
        (d / str(fid)).mkdir()


def test_choose_small_pull_takes_shortest_recorded(tmp_path):
    _write_report(tmp_path, "AAA", [(1, 90_000), (2, 30_000)], recorded=[1])  # fight 2 is shorter but not recorded
    _write_report(tmp_path, "BBB", [(7, 50_000)], recorded=[7])
    assert pb.choose_small_pull(tmp_path) == ("BBB", 7)


def test_choose_small_pull_with_nothing_recorded(tmp_path):
    with pytest.raises(pb.ProbeError):
        pb.choose_small_pull(tmp_path)


def test_ask_requires_typed_yes(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _prompt: "yes")
    assert pb.ask("go?") is True
    monkeypatch.setattr("builtins.input", lambda _prompt: "")
    assert pb.ask("go?") is False

    def eof(_prompt):
        raise EOFError

    monkeypatch.setattr("builtins.input", eof)
    assert pb.ask("go?") is False
