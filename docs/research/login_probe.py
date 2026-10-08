"""Rerun the login-status measurements behind cli-login-preflight.md.

Each backend's native status check runs against throwaway homes holding no
login, an expired OAuth login whose refresh token is dead, and a bogus API key.
Nothing here reads or writes your real credentials, and nothing calls a model.

    python docs/research/login_probe.py

Hermes is left out: a fresh HERMES_HOME runs its first-run installer, which
blocks on an install lock instead of answering.
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

EXPIRED_MS = 1_600_000_000_000


def _jwt(claims: dict) -> str:
    def part(obj: dict) -> str:
        raw = json.dumps(obj).encode()
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    return f"{part({'alg': 'RS256', 'typ': 'JWT'})}.{part(claims)}.c2ln"


def _run(cmd: list[str], env: dict[str, str], stdin: str | None = None) -> str:
    started = time.monotonic()
    try:
        proc = subprocess.run(
            cmd,
            env={**os.environ, **env},
            input=stdin,
            stdin=None if stdin is not None else subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        return "timed out after 30s"
    said = " ".join((proc.stdout + proc.stderr).split())[:110]
    return f"exit {proc.returncode}, {time.monotonic() - started:.2f}s: {said}"


def _codex_account(env: dict[str, str]) -> str:
    """Ask codex app-server for the account after a forced token refresh."""
    rpc = [
        {
            "id": 1,
            "method": "initialize",
            "params": {"clientInfo": {"name": "login-probe", "version": "0"}},
        },
        {"method": "initialized"},
        {"id": 2, "method": "account/read", "params": {"refreshToken": True}},
    ]
    proc = subprocess.Popen(
        ["codex", "app-server"],
        env={**os.environ, **env},
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    assert proc.stdin
    assert proc.stdout
    started = time.monotonic()
    for message in rpc:
        proc.stdin.write(json.dumps({"jsonrpc": "2.0", **message}) + "\n")
    proc.stdin.flush()
    for line in proc.stdout:
        reply = json.loads(line)
        if reply.get("id") == 2:
            break
    proc.terminate()
    account = (reply.get("result") or {}).get("account")
    kind = account["type"] if account else None
    return f"{time.monotonic() - started:.2f}s: account={kind}"


def claude(root: Path) -> None:
    expired = root / "expired"
    expired.mkdir()
    oauth = {
        "accessToken": "sk-ant-oat01-dead",
        "refreshToken": "sk-ant-ort01-dead",
        "expiresAt": EXPIRED_MS,
        "scopes": ["user:inference"],
    }
    (expired / ".credentials.json").write_text(json.dumps({"claudeAiOauth": oauth}))
    status = ["claude", "auth", "status"]
    print("claude auth status")
    print("  no login      ", _run(status, {"CLAUDE_CONFIG_DIR": str(root)}))
    print("  expired OAuth ", _run(status, {"CLAUDE_CONFIG_DIR": str(expired)}))
    bogus = {"CLAUDE_CONFIG_DIR": str(root), "ANTHROPIC_API_KEY": "sk-ant-bogus"}
    print("  bogus API key ", _run(status, bogus))


def codex(root: Path) -> None:
    empty, expired, keyed = root / "empty", root / "expired", root / "key"
    for home in (empty, expired, keyed):
        home.mkdir()
    claims = {"exp": EXPIRED_MS // 1000, "https://api.openai.com/auth": {}}
    tokens = {
        "id_token": _jwt(claims),
        "access_token": _jwt(claims),
        "refresh_token": "rt_dead",
        "account_id": "acct",
    }
    (expired / "auth.json").write_text(
        json.dumps({"auth_mode": "chatgpt", "OPENAI_API_KEY": None, "tokens": tokens})
    )
    (keyed / "auth.json").write_text(
        json.dumps({"auth_mode": "apikey", "OPENAI_API_KEY": "sk-bogus"})
    )
    status = ["codex", "login", "status"]
    print("codex login status")
    print("  no login      ", _run(status, {"CODEX_HOME": str(empty)}))
    print("  expired OAuth ", _run(status, {"CODEX_HOME": str(expired)}))
    print("  bogus API key ", _run(status, {"CODEX_HOME": str(keyed)}))
    print("codex app-server account/read {refreshToken: true}")
    print("  expired OAuth ", _codex_account({"CODEX_HOME": str(expired)}))
    print("  bogus API key ", _codex_account({"CODEX_HOME": str(keyed)}))


def pi(root: Path) -> None:
    empty, expired, keyed = root / "empty", root / "expired", root / "key"
    for home in (empty, expired, keyed):
        home.mkdir()
    oauth = {
        "type": "oauth",
        "access": "sk-ant-oat01-dead",
        "refresh": "sk-ant-ort01-dead",
        "expires": EXPIRED_MS,
    }
    (expired / "auth.json").write_text(json.dumps({"anthropic": oauth}))
    key = {"type": "api_key", "key": "sk-ant-bogus"}
    (keyed / "auth.json").write_text(json.dumps({"anthropic": key}))
    check = ["pi", "auth", "check", "--provider", "anthropic", "--json"]
    print("pi auth check --provider anthropic")
    print("  no login      ", _run(check, {"PI_CODING_AGENT_DIR": str(empty)}))
    print(
        "  expired, --no-refresh",
        _run([*check, "--no-refresh"], {"PI_CODING_AGENT_DIR": str(expired)}),
    )
    print("  expired OAuth ", _run(check, {"PI_CODING_AGENT_DIR": str(expired)}))
    print("  bogus API key ", _run(check, {"PI_CODING_AGENT_DIR": str(keyed)}))


def main() -> None:
    for name, probe in (("claude", claude), ("codex", codex), ("pi", pi)):
        if shutil.which(name) is None:
            print(f"{name}: not installed, skipped")
            continue
        with tempfile.TemporaryDirectory(prefix=f"login-probe-{name}-") as tmp:
            probe(Path(tmp))


if __name__ == "__main__":
    main()
