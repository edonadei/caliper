"""Caliper's own Sign in with ChatGPT login, against a fake auth.openai.com.

The fake speaks the documented open-source flow
(developers.openai.com/siwc/token-sharing-open-source): dynamic registration at
the authorize endpoint, a PKCE code exchange, rotating refresh tokens and
revocation. The "browser" is a plain HTTP client that follows the authorize
redirect back to caliper's loopback callback.
"""

from __future__ import annotations

import base64
import hashlib
import json
import stat
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import pytest

from caliper.chatgpt import PLAN_SCOPE, ChatGPTAccount, ChatGPTLoginError
from caliper.harness.base import HarnessConfigurationError, LoginRequired

FULL_SCOPE = f"openid profile email offline_access resource.invoke {PLAN_SCOPE}"


def _jwt(claims: dict) -> str:
    def part(value: dict) -> str:
        raw = json.dumps(value).encode()
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    return f"{part({'alg': 'RS256'})}.{part(claims)}.c2lnbmF0dXJl"


class FakeAuth:
    """auth.openai.com as the docs describe it, and a record of what it saw."""

    def __init__(self) -> None:
        self.client_id = "client-issued-1"
        self.email = "you@example.com"
        self.granted_scope = FULL_SCOPE
        self.expires_in = 3600
        self.authorize_params: list[dict[str, str]] = []
        self.token_requests: list[dict[str, str]] = []
        self.revoked: list[dict[str, str]] = []
        # A refresh error to answer with: (status, body).
        self.refresh_error: tuple[int, dict] | None = None
        self.refresh_delay = 0.0
        self.deny = False
        self._codes: dict[str, dict[str, str]] = {}
        self._live_refresh: str | None = None
        self._serial = 0
        self._lock = threading.Lock()
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args) -> None:
                pass

            def do_GET(self) -> None:
                url = urlparse(self.path)
                if url.path == "/api/accounts/authorize":
                    fake._authorize(
                        self, dict(parse_qs(url.query, strict_parsing=True))
                    )
                elif url.path == "/.well-known/openid-configuration":
                    fake._json(
                        self,
                        200,
                        {"revocation_endpoint": f"{fake.origin}/oauth/revoke"},
                    )
                else:
                    fake._json(self, 404, {})

            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                form = {
                    k: v[0]
                    for k, v in parse_qs(self.rfile.read(length).decode()).items()
                }
                if self.path == "/api/accounts/oauth/token":
                    fake._token(self, form)
                elif self.path == "/oauth/revoke":
                    fake.revoked.append(form)
                    self.send_response(200)
                    self.end_headers()
                else:
                    fake._json(self, 404, {})

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.origin = f"http://127.0.0.1:{self._server.server_port}"
        threading.Thread(
            target=self._server.serve_forever,
            kwargs={"poll_interval": 0.05},
            daemon=True,
        ).start()

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()

    def _json(self, handler, status: int, body: dict) -> None:
        payload = json.dumps(body).encode()
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(payload)))
        handler.end_headers()
        handler.wfile.write(payload)

    def _authorize(self, handler, query: dict[str, list[str]]) -> None:
        params = {k: v[0] for k, v in query.items()}
        self.authorize_params.append(params)
        code = f"code-{len(self.authorize_params)}"
        self._codes[code] = params
        reply = {"code": code, "state": params["state"], "scope": self.granted_scope}
        if self.deny:
            reply = {"error": "access_denied", "state": params["state"]}
        if params["client_id"] == "dynamic_agent_client":
            reply["client_id"] = self.client_id
        handler.send_response(302)
        handler.send_header("Location", f"{params['redirect_uri']}?{urlencode(reply)}")
        handler.end_headers()

    def _tokens(self, nonce: str | None) -> dict:
        self._serial += 1
        self._live_refresh = f"refresh-{self._serial}"
        claims = {
            "iss": self.origin,
            "aud": self.client_id,
            "sub": "user-sub-1",
            "email": self.email,
            "exp": int(time.time()) + 3600,
        }
        if nonce is not None:
            claims["nonce"] = nonce
        return {
            "access_token": f"access-{self._serial}",
            "refresh_token": self._live_refresh,
            "id_token": _jwt(claims),
            "token_type": "Bearer",
            "expires_in": self.expires_in,
            "scope": self.granted_scope,
        }

    def _token(self, handler, form: dict[str, str]) -> None:
        self.token_requests.append(form)
        if form["grant_type"] == "authorization_code":
            params = self._codes.pop(form["code"])
            challenge = (
                base64.urlsafe_b64encode(
                    hashlib.sha256(form["code_verifier"].encode()).digest()
                )
                .rstrip(b"=")
                .decode()
            )
            assert challenge == params["code_challenge"]
            assert form["redirect_uri"] == params["redirect_uri"]
            assert form["client_id"] == self.client_id
            self._json(handler, 200, self._tokens(params["nonce"]))
            return
        time.sleep(self.refresh_delay)
        with self._lock:
            if self.refresh_error is not None:
                self._json(handler, *self.refresh_error)
            elif form["refresh_token"] != self._live_refresh:
                self._json(handler, 400, {"error": "refresh_token_reused"})
            else:
                self._json(handler, 200, self._tokens(None))


@pytest.fixture
def auth():
    fake = FakeAuth()
    yield fake
    fake.close()


def browser(url: str) -> None:
    """Follows the authorize redirect to the loopback callback, as a browser would."""
    with urllib.request.urlopen(url, timeout=10) as reply:
        reply.read()


class Clock:
    """Real time, plus however far a test has moved it on."""

    def __init__(self) -> None:
        self.ahead = 0.0

    def __call__(self) -> float:
        return time.time() + self.ahead


@pytest.fixture
def clock():
    return Clock()


def account(auth: FakeAuth, home: Path, clock: Clock | None = None) -> ChatGPTAccount:
    return ChatGPTAccount(
        home, auth_origin=auth.origin, callback_port=0, clock=clock or time.time
    )


def test_a_first_sign_in_registers_caliper_and_stores_the_login(auth, tmp_path) -> None:
    signed_in = account(auth, tmp_path)

    assert signed_in.sign_in(browser) == "you@example.com"

    params = auth.authorize_params[0]
    assert params["client_id"] == "dynamic_agent_client"
    assert params["agent_name_hint"] == "Caliper"
    assert params["ext_agent_host_id"].startswith("urn:uuid:")
    assert params["scope"] == FULL_SCOPE
    assert params["resource"] == "https://api.openai.com/v1"
    assert params["code_challenge_method"] == "S256"
    assert urlparse(params["redirect_uri"]).hostname == "127.0.0.1"
    assert urlparse(params["redirect_uri"]).path == "/auth/callback"
    # A fresh account object reads the same login back from disk.
    assert account(auth, tmp_path).signed_in
    assert account(auth, tmp_path).access_token(lasting=60) == "access-1"
    if sys.platform != "win32":
        mode = stat.S_IMODE((tmp_path / "chatgpt.json").stat().st_mode)
        assert mode == 0o600


def test_a_later_sign_in_reuses_the_issued_client_and_host(auth, tmp_path) -> None:
    account(auth, tmp_path).sign_in(browser)

    account(auth, tmp_path).sign_in(browser)

    first, again = auth.authorize_params
    assert again["client_id"] == "client-issued-1"
    assert "agent_name_hint" not in again
    assert again["login_hint"] == "you@example.com"
    assert again["ext_agent_host_id"] == first["ext_agent_host_id"]
    assert account(auth, tmp_path).access_token(lasting=60) == "access-2"


def test_a_sign_in_without_plan_usage_says_how_to_allow_it(auth, tmp_path) -> None:
    auth.granted_scope = "openid profile email offline_access resource.invoke"

    with pytest.raises(ChatGPTLoginError, match="allow"):
        account(auth, tmp_path).sign_in(browser)

    # Nothing changed: codex keeps running on the Codex CLI's own login.
    assert not account(auth, tmp_path).signed_in


def test_a_token_about_to_expire_is_refreshed_and_the_rotation_kept(
    auth, tmp_path, clock
) -> None:
    account(auth, tmp_path, clock).sign_in(browser)
    clock.ahead = 3570

    # Good for 30 more seconds, but the attempt may run for two minutes.
    assert account(auth, tmp_path, clock).access_token(lasting=120) == "access-2"

    refresh = auth.token_requests[-1]
    assert refresh == {
        "grant_type": "refresh_token",
        "client_id": "client-issued-1",
        "refresh_token": "refresh-1",
        "resource": "https://api.openai.com/v1",
    }
    # The rotated refresh token is the one stored: the next refresh uses it.
    clock.ahead += 3570
    assert account(auth, tmp_path, clock).access_token(lasting=120) == "access-3"
    assert auth.token_requests[-1]["refresh_token"] == "refresh-2"


def test_a_token_that_lasts_the_attempt_is_not_refreshed(auth, tmp_path) -> None:
    account(auth, tmp_path).sign_in(browser)

    assert account(auth, tmp_path).access_token(lasting=120) == "access-1"
    assert [r["grant_type"] for r in auth.token_requests] == ["authorization_code"]


def test_attempts_starting_together_refresh_once(auth, tmp_path, clock) -> None:
    account(auth, tmp_path, clock).sign_in(browser)
    clock.ahead = 3570
    # Slow enough that every worker asks while the first refresh is in flight.
    auth.refresh_delay = 0.3
    tokens: list[str] = []

    def attempt() -> None:
        # One account object per worker, as separate processes would have.
        tokens.append(account(auth, tmp_path, clock).access_token(lasting=120))

    workers = [threading.Thread(target=attempt) for _ in range(4)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()

    assert tokens == ["access-2"] * 4
    refreshes = [r for r in auth.token_requests if r["grant_type"] == "refresh_token"]
    assert len(refreshes) == 1


def test_an_attempt_longer_than_a_token_takes_a_new_one_without_churning(
    auth, tmp_path, clock
) -> None:
    signed_in = account(auth, tmp_path, clock)
    signed_in.sign_in(browser)

    # No token lasts two hours: a just-issued one is the best there is.
    assert signed_in.access_token(lasting=7200) == "access-1"
    clock.ahead = 600
    assert signed_in.access_token(lasting=7200) == "access-2"
    assert signed_in.access_token(lasting=7200) == "access-2"


@pytest.mark.parametrize(
    "code", ["refresh_token_reused", "refresh_token_expired", "invalid_grant"]
)
def test_a_refused_refresh_asks_for_a_new_sign_in(auth, tmp_path, clock, code) -> None:
    account(auth, tmp_path, clock).sign_in(browser)
    clock.ahead = 3570
    auth.refresh_error = (400, {"error": code})

    with pytest.raises(LoginRequired) as exc:
        account(auth, tmp_path, clock).access_token(lasting=120)

    assert exc.value.backend == "codex"
    assert exc.value.command == ["caliper", "login", "chatgpt"]
    assert code in str(exc.value)
    # Still signed in: a lapsed login must not quietly fall back to another one.
    assert account(auth, tmp_path).signed_in


def test_a_refused_refresh_is_not_sent_again_by_the_next_attempt(
    auth, tmp_path, clock
) -> None:
    account(auth, tmp_path, clock).sign_in(browser)
    clock.ahead = 3570
    auth.refresh_error = (400, {"error": "refresh_token_reused"})
    for _ in range(3):
        with pytest.raises(LoginRequired, match="refresh_token_reused"):
            account(auth, tmp_path, clock).access_token(lasting=120)

    refreshes = [r for r in auth.token_requests if r["grant_type"] == "refresh_token"]
    assert len(refreshes) == 1
    # A new sign-in clears it.
    auth.refresh_error = None
    account(auth, tmp_path, clock).sign_in(browser)
    assert account(auth, tmp_path, clock).access_token(lasting=120) == "access-2"


def test_an_unreachable_auth_server_is_not_a_lapsed_login(
    auth, tmp_path, clock, monkeypatch
) -> None:
    monkeypatch.setattr("caliper.chatgpt.time.sleep", lambda _seconds: None)
    account(auth, tmp_path, clock).sign_in(browser)
    clock.ahead = 3570
    auth.refresh_error = (503, {})

    with pytest.raises(HarnessConfigurationError) as exc:
        account(auth, tmp_path, clock).access_token(lasting=120)

    assert not isinstance(exc.value, LoginRequired)
    refreshes = [r for r in auth.token_requests if r["grant_type"] == "refresh_token"]
    assert len(refreshes) == 3


def test_an_unknown_client_needs_a_new_registration(auth, tmp_path, clock) -> None:
    account(auth, tmp_path, clock).sign_in(browser)
    clock.ahead = 3570
    auth.refresh_error = (401, {"error": "invalid_client"})

    with pytest.raises(HarnessConfigurationError, match="caliper logout chatgpt"):
        account(auth, tmp_path, clock).access_token(lasting=120)


def test_signing_out_revokes_the_session_and_forgets_it(auth, tmp_path) -> None:
    account(auth, tmp_path).sign_in(browser)

    assert account(auth, tmp_path).sign_out() is True

    assert auth.revoked == [
        {
            "token": "refresh-1",
            "token_type_hint": "refresh_token",
            "client_id": "client-issued-1",
        }
    ]
    assert not account(auth, tmp_path).signed_in
    # The machine keeps its host ID for the next sign-in.
    assert (tmp_path / "host-id").is_file()


def test_signing_out_offline_still_forgets_the_login(
    auth, tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr("caliper.chatgpt.time.sleep", lambda _seconds: None)
    account(auth, tmp_path).sign_in(browser)
    auth.close()

    assert account(auth, tmp_path).sign_out() is False
    assert not account(auth, tmp_path).signed_in


def test_a_declined_sign_in_stores_nothing(auth, tmp_path) -> None:
    auth.deny = True

    with pytest.raises(ChatGPTLoginError, match="access_denied"):
        account(auth, tmp_path).sign_in(browser)

    assert auth.token_requests == []
    assert not account(auth, tmp_path).signed_in
