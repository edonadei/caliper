"""Caliper's own Sign in with ChatGPT login, for running codex on a ChatGPT plan.

Caliper is its own OAuth client in OpenAI's open-source tier
(developers.openai.com/siwc/token-sharing-open-source), so the login and its
rotating refresh token live under caliper's config, never in ``~/.codex``. Only
caliper's process refreshes, one refresh at a time; an attempt is handed a
short-lived access token and nothing it can refresh or use up
(docs/adr/0039-caliper-owns-the-chatgpt-plan-login.md).
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import shlex
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

from caliper.harness.base import HarnessConfigurationError, LoginRequired

AUTH_ORIGIN = "https://auth.openai.com"
RESOURCE = "https://api.openai.com/v1"
# The grant that lets requests run on the user's plan; checked in the token
# response's scopes, never in the ID token.
PLAN_SCOPE = "chatgpt.tokens.use.direct"
SCOPES = f"openid profile email offline_access resource.invoke {PLAN_SCOPE}"
APP_NAME = "Caliper"
LOGIN_COMMAND = ["caliper", "login", "chatgpt"]

# Codex's own login port; any other works for a later sign-in, as long as the
# scheme, host and path stay the same.
CALLBACK_PORT = 1455
CALLBACK_PATH = "/auth/callback"
SIGN_IN_TIMEOUT = 300
# Seconds between tries when the auth server is down or busy; ``None`` ends.
REFRESH_BACKOFF = (1, 3, None)
ACCESS_TOKEN_LIFETIME = 3600


class ChatGPTLoginError(RuntimeError):
    """Signing in, or out, did not complete."""


def config_home() -> Path:
    """Where caliper keeps its own config: the platform's per-user config dir."""
    if sys.platform == "win32" and os.environ.get("APPDATA"):
        return Path(os.environ["APPDATA"]) / "caliper"
    base = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(base) / "caliper"


def chatgpt_account() -> ChatGPTAccount:
    """The login every run uses."""
    return ChatGPTAccount(config_home())


class ChatGPTAccount:
    """One ChatGPT login, stored as ``chatgpt.json`` under ``home``.

    The host ID sits in its own file beside it, so a login copied to another
    machine keeps that machine's ID.
    """

    def __init__(
        self,
        home: Path,
        *,
        auth_origin: str = AUTH_ORIGIN,
        callback_port: int = CALLBACK_PORT,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._home = Path(home)
        self._clock = clock
        self._origin = auth_origin
        self._callback_port = callback_port
        self.path = self._home / "chatgpt.json"

    @property
    def signed_in(self) -> bool:
        return self.path.is_file()

    # --- signing in --------------------------------------------------------

    def sign_in(self, open_browser: Callable[[str], object]) -> str:
        """Run the browser sign-in and store the login; return its email.

        A first sign-in registers caliper as a client; a later one reuses the
        client it was issued.
        """
        saved = self._load() if self.signed_in else {}
        client_id = saved.get("client_id")
        state, nonce = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)
        callback = _Callback(self._callback_port)
        try:
            params = {
                "client_id": client_id or "dynamic_agent_client",
                "ext_agent_host_id": self._host_id(),
                "response_type": "code",
                "redirect_uri": callback.redirect_uri,
                "scope": SCOPES,
                "resource": RESOURCE,
                "state": state,
                "nonce": nonce,
                "code_challenge_method": "S256",
                "code_challenge": _challenge(verifier),
            }
            if client_id:
                params["login_hint"] = saved.get("email", "")
            else:
                params["agent_name_hint"] = APP_NAME
            open_browser(f"{self._origin}/api/accounts/authorize?{urlencode(params)}")
            reply = callback.wait(SIGN_IN_TIMEOUT)
        finally:
            callback.close()

        if reply.get("state") != state:
            raise ChatGPTLoginError("The sign-in reply did not match this sign-in.")
        if "error" in reply:
            raise ChatGPTLoginError(f"ChatGPT did not sign you in ({reply['error']}).")
        issued = reply.get("client_id")
        if client_id and issued and issued != client_id:
            raise ChatGPTLoginError(
                "ChatGPT answered for a different caliper registration."
            )
        client_id = client_id or issued
        if not client_id:
            raise ChatGPTLoginError("ChatGPT did not finish registering caliper.")

        tokens = self._post_token(
            {
                "grant_type": "authorization_code",
                "client_id": client_id,
                "code": reply.get("code", ""),
                "code_verifier": verifier,
                "redirect_uri": callback.redirect_uri,
                "resource": RESOURCE,
            }
        )
        identity = self._identity(tokens.get("id_token"), client_id, nonce)
        if saved.get("subject") not in (None, identity["sub"]):
            raise ChatGPTLoginError(
                "You signed in to a different ChatGPT account. Run `caliper "
                "logout chatgpt` first to switch accounts."
            )
        if PLAN_SCOPE not in tokens.get("scope", "").split():
            # Stored, the login would stop every codex run; unstored, codex
            # keeps the Codex CLI's own login.
            raise ChatGPTLoginError(
                "You signed in, but did not allow caliper to use your ChatGPT "
                "plan. Run `caliper login chatgpt` again and allow it."
            )
        record = {
            "client_id": client_id,
            "issuer": identity["iss"],
            "subject": identity["sub"],
            "email": identity.get("email"),
            "id_token": tokens["id_token"],
        }
        # Under the lock: a run refreshing the old login would otherwise
        # overwrite this one with its rotated tokens.
        with self._locked():
            self._save(_with_tokens(record, tokens, self._clock()))
        return identity.get("email") or identity["sub"]

    def _identity(self, id_token: str | None, client_id: str, nonce: str) -> dict:
        """The ID token's claims, once they prove it was issued for this sign-in.

        No signature check: the token came straight from the token endpoint over
        TLS, which OIDC Core 3.1.3.7 accepts in its place.
        """
        try:
            payload = id_token.split(".")[1]
            claims = json.loads(base64.urlsafe_b64decode(payload + "=" * 4))
        except (AttributeError, IndexError, ValueError) as exc:
            raise ChatGPTLoginError("ChatGPT returned no usable ID token.") from exc
        if not isinstance(claims, dict):
            raise ChatGPTLoginError("ChatGPT returned no usable ID token.")
        audience = claims.get("aud")
        audiences = audience if isinstance(audience, list) else [audience]
        if (
            claims.get("iss") != self._origin
            or client_id not in audiences
            or claims.get("nonce") != nonce
            or not isinstance(claims.get("exp"), (int, float))
            or claims["exp"] < self._clock()
            or not claims.get("sub")
        ):
            raise ChatGPTLoginError("ChatGPT returned an ID token for another sign-in.")
        return claims

    # --- using the login ---------------------------------------------------

    def access_token(self, *, lasting: int) -> str:
        """An access token good for at least ``lasting`` more seconds.

        The refresh token rotates and a reused one is refused, so callers take
        turns, each reading the file afresh: the one before may already have
        refreshed, or been refused.
        """
        with self._locked():
            record = self._load()
            if record.get("lapsed"):
                raise _lapsed(record["lapsed"])
            if PLAN_SCOPE not in record.get("scope", "").split():
                raise _lapsed("it may not use your ChatGPT plan")
            if not _good_for(record, lasting, self._clock()):
                record = self._refresh(record)
            return record["access_token"]

    @contextmanager
    def _locked(self) -> Iterator[None]:
        """Hold the login against every other refresh, in any caliper process."""
        self._home.mkdir(parents=True, exist_ok=True)
        with _THREAD_LOCK, open(self._home / "chatgpt.lock", "a+b") as handle:
            _lock_file(handle)
            try:
                yield
            finally:
                _unlock_file(handle)

    def _refresh(self, record: dict) -> dict:
        """Trade the stored refresh token for new tokens, and store them.

        A refused refresh leaves the login in place: it takes a new sign-in,
        and until then every run says so rather than quietly using another
        login.
        """
        form = {
            "grant_type": "refresh_token",
            "client_id": record["client_id"],
            "refresh_token": record["refresh_token"],
            "resource": RESOURCE,
        }
        for delay in REFRESH_BACKOFF:
            try:
                status, body = _post(f"{self._origin}/api/accounts/oauth/token", form)
            except OSError as exc:
                status, body = None, {"error": str(exc)}
            if status is not None and status < 500 and status != 429:
                break
            if delay is not None:
                time.sleep(delay)
        else:
            raise HarnessConfigurationError(
                "caliper could not refresh your ChatGPT login: "
                f"{self._origin} did not answer "
                f"({status or _error_code(body) or 'no reply'}).\n\n"
                "Your login is kept. Check your connection, then rerun the eval."
            )
        code = _error_code(body)
        if code == "invalid_client":
            raise HarnessConfigurationError(
                "ChatGPT no longer knows caliper's registration (invalid_client)."
                "\n\nRun `caliper logout chatgpt`, then `caliper login chatgpt` to "
                "register again, and rerun the eval."
            )
        if status != 200 or not body.get("access_token"):
            # Recorded, so no later attempt sends the spent token again.
            reason = code or f"HTTP {status}"
            self._save({**record, "lapsed": reason})
            raise _lapsed(reason)
        record = _with_tokens(record, body, self._clock())
        self._save(record)
        return record

    # --- signing out -------------------------------------------------------

    def sign_out(self) -> bool:
        """Revoke the session at OpenAI and forget the login.

        True when OpenAI confirmed the revocation. The login is forgotten
        either way; the caller tells the user when it was not confirmed.
        """
        with self._locked():
            record = self._load()
            revoked = self._revoke(record)
            self.path.unlink()
        return revoked

    def _revoke(self, record: dict) -> bool:
        form = {
            "token": record["refresh_token"],
            "token_type_hint": "refresh_token",
            "client_id": record["client_id"],
        }
        for delay in REFRESH_BACKOFF:
            try:
                discovery = _get_json(
                    f"{self._origin}/.well-known/openid-configuration"
                )
                status, _body = _post(discovery["revocation_endpoint"], form)
            except (OSError, KeyError, ValueError):
                status = None
            if status == 200:
                return True
            if status is not None and status < 500:
                return False
            if delay is not None:
                time.sleep(delay)
        return False

    # --- the token endpoint and the files ----------------------------------

    def _post_token(self, form: dict[str, str]) -> dict:
        try:
            status, body = _post(f"{self._origin}/api/accounts/oauth/token", form)
        except OSError as exc:
            raise ChatGPTLoginError(f"Could not reach {self._origin}: {exc}") from exc
        if status != 200:
            raise ChatGPTLoginError(
                f"ChatGPT refused the sign-in ({_error_code(body) or status})."
            )
        return body

    def _host_id(self) -> str:
        """This machine's stable, opaque ID, created before its first sign-in."""
        path = self._home / "host-id"
        if path.is_file():
            return path.read_text(encoding="utf-8").strip()
        host_id = f"urn:uuid:{uuid.uuid4()}"
        _write_private(path, host_id + "\n")
        return host_id

    def _load(self) -> dict:
        return json.loads(self.path.read_text(encoding="utf-8"))

    def _save(self, record: dict) -> None:
        _write_private(self.path, json.dumps(record, indent=2) + "\n")


# File locks don't reliably exclude two threads of one process from each other.
_THREAD_LOCK = threading.Lock()


def _lock_file(handle) -> None:
    """Block until this process holds ``handle``'s file; released on close."""
    if sys.platform == "win32":
        import msvcrt

        # msvcrt locks bytes from the current position: always the first one.
        handle.seek(0)
        while True:
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                return
            except OSError:
                time.sleep(0.05)
    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)


def _unlock_file(handle) -> None:
    if sys.platform == "win32":
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        return
    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _good_for(record: dict, lasting: int, now: float) -> bool:
    """True when the stored access token needs no refresh to last ``lasting``.

    Also when it was only just issued: an attempt longer than a token's lifetime
    is not served better by a refresh, and every attempt would take one.
    """
    left = record["expires_at"] - now
    return left >= lasting or left >= 0.9 * record.get("lifetime", 0)


def _lapsed(reason: str) -> LoginRequired:
    return LoginRequired(
        f"Your ChatGPT login for caliper has lapsed ({reason}).\n\n"
        f"Run `{shlex.join(LOGIN_COMMAND)}`, then rerun the eval.",
        backend="codex",
        command=LOGIN_COMMAND,
    )


def _with_tokens(record: dict, tokens: dict, now: float) -> dict:
    """``record`` with a token response's tokens, replaced together.

    A response without ``expires_in`` is taken at the documented hour.
    """
    lifetime = float(tokens.get("expires_in", ACCESS_TOKEN_LIFETIME))
    record = {
        **record,
        "access_token": tokens["access_token"],
        "refresh_token": tokens.get("refresh_token", record.get("refresh_token")),
        "token_type": tokens.get("token_type", "Bearer"),
        "expires_at": now + lifetime,
        "lifetime": lifetime,
        "scope": tokens.get("scope", record.get("scope", "")),
        "saved_at": now,
    }
    if tokens.get("id_token"):
        record["id_token"] = tokens["id_token"]
    return record


def _challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _post(url: str, form: dict[str, str]) -> tuple[int, dict]:
    """POST a form; the status and the JSON body (empty when there is none)."""
    request = urllib.request.Request(
        url,
        data=urlencode(form).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as reply:
            status, raw = reply.status, reply.read()
    except urllib.error.HTTPError as exc:
        status, raw = exc.code, exc.read()
    try:
        body = json.loads(raw) if raw else {}
    except ValueError:
        body = {}
    return status, body if isinstance(body, dict) else {}


def _get_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=30) as reply:
        body = json.loads(reply.read())
    if not isinstance(body, dict):
        raise ValueError(f"{url} did not answer with an object")
    return body


def _error_code(body: dict) -> str | None:
    error = body.get("error")
    if isinstance(error, dict):
        error = error.get("code")
    return error if isinstance(error, str) else None


def _write_private(path: Path, text: str) -> None:
    """Write ``text`` atomically, readable by the owner only."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(text)
    os.replace(temp, path)


class _Callback:
    """The loopback listener the browser is sent back to, started before it."""

    def __init__(self, port: int) -> None:
        self._reply: dict[str, str] | None = None
        self._done = threading.Event()
        listener = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args) -> None:
                pass

            def do_GET(self) -> None:
                url = urlparse(self.path)
                if url.path != CALLBACK_PATH or listener._done.is_set():
                    self.send_response(404)
                    self.end_headers()
                    return
                query = parse_qs(url.query)
                listener._reply = {k: v[0] for k, v in query.items()}
                page = b"Signed in to caliper. You can close this tab.\n"
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(page)))
                self.end_headers()
                self.wfile.write(page)
                listener._done.set()

        try:
            self._server = HTTPServer(("127.0.0.1", port), Handler)
        except OSError:
            # Taken, say by a codex login in progress.
            self._server = HTTPServer(("127.0.0.1", 0), Handler)
        self.redirect_uri = (
            f"http://127.0.0.1:{self._server.server_port}{CALLBACK_PATH}"
        )
        threading.Thread(
            target=self._server.serve_forever,
            kwargs={"poll_interval": 0.05},
            daemon=True,
        ).start()

    def wait(self, timeout: float) -> dict[str, str]:
        if not self._done.wait(timeout):
            raise ChatGPTLoginError(
                f"No sign-in arrived within {timeout // 60:.0f} minutes."
            )
        return self._reply or {}

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
