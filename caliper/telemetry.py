"""Opt-in command counts. Only the fields constructed here leave the machine.

No SDK, identity, retries or event queue. See docs/telemetry.md for the payload
and the PostHog project settings required before enabling a release.
"""

from __future__ import annotations

import http.client
import json
import os
import re
import sys
import tempfile
import threading
import time
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Literal

import click
import typer
from typer.core import TyperCommand

from caliper import config
from caliper.backends import DEFAULT_BACKEND, VALID_BACKENDS
from caliper.schema.spec import parse_target

# Public, ingestion-only token for the Caliper project.
# Never put a personal API key here or in CALIPER_TELEMETRY_PROJECT_TOKEN.
POSTHOG_PROJECT_TOKEN = "phc_oBpkgiYCN3XSHrA6VxgFpgaTzYhQkLhLPpKRJt2RLgrG"
POSTHOG_HOST = "https://us.i.posthog.com"
POSTHOG_HOSTS = {"https://eu.i.posthog.com", "https://us.i.posthog.com"}
DELIVERY_BUDGET_SECONDS = 0.2
COMMANDS = frozenset(
    {"run", "report", "compare", "list", "validate", "login", "logout"}
)
Outcome = Literal[
    "success", "bad_input", "cannot_run", "bar_not_met", "interrupted", "error"
]


def enabled() -> bool:
    """Malformed, missing or unreadable consent always means no collection."""
    try:
        consent = json.loads((config.config_home() / "telemetry.json").read_text())
        return isinstance(consent, dict) and consent.get("enabled") is True
    except (OSError, ValueError, RuntimeError):
        return False


def set_enabled(value: bool) -> None:
    home = config.config_home()
    home.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", dir=home, delete=False) as stream:
            temporary = Path(stream.name)
            json.dump({"enabled": value}, stream)
        os.replace(temporary, home / "telemetry.json")
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def forced_off() -> bool:
    return bool(os.environ.get("CALIPER_TELEMETRY_DISABLED"))


def destination() -> tuple[str, str] | None:
    token = os.environ.get("CALIPER_TELEMETRY_PROJECT_TOKEN", POSTHOG_PROJECT_TOKEN)
    host = os.environ.get("CALIPER_TELEMETRY_HOST", POSTHOG_HOST)
    if host not in POSTHOG_HOSTS or not re.fullmatch(r"phc_[A-Za-z0-9]+", token):
        return None
    return host.removeprefix("https://"), token


def _duration_bucket(seconds: float) -> str:
    for threshold, bucket in (
        (1, "lt_1s"),
        (10, "1s_10s"),
        (60, "10s_60s"),
        (600, "1m_10m"),
    ):
        if seconds < threshold:
            return bucket
    return "ge_10m"


def _release_version() -> str:
    try:
        release = version("caliper-eval")
    except PackageNotFoundError:
        return "unknown"
    # Local version labels can contain a developer's branch name or identity.
    return (
        release
        if re.fullmatch(r"\d{1,5}\.\d{1,5}\.\d{1,5}(?:(?:a|b|rc)\d{1,5})?", release)
        else "unknown"
    )


def _payload(
    command: str, backend: str | None, outcome: Outcome, seconds: float, token: str
) -> bytes:
    return json.dumps(
        {
            "api_key": token,
            "event": "caliper_command_completed",
            # One shared value, never an installation ID. Unique-user charts are meaningless.
            "distinct_id": "caliper-anonymous",
            "properties": {
                "schema_version": 1,
                "command": command,
                "caliper_version": _release_version(),
                "os_family": {
                    "darwin": "macos",
                    "win32": "windows",
                    "linux": "linux",
                }.get(sys.platform, "other"),
                "backend": backend,
                "outcome": outcome,
                "duration_bucket": _duration_bucket(seconds),
                "$process_person_profile": False,
                "$geoip_disable": True,
                "$ip": "0.0.0.0",
            },
        },
        separators=(",", ":"),
    ).encode("utf-8")


def _post(host: str, body: bytes) -> None:
    try:
        connection = http.client.HTTPSConnection(host, timeout=DELIVERY_BUDGET_SECONDS)
        try:
            connection.request(
                "POST",
                "/i/v0/e/",
                body,
                {
                    "Content-Type": "application/json",
                    "User-Agent": "caliper-telemetry/1",
                },
            )
            connection.getresponse().close()
        finally:
            connection.close()
    except Exception:
        # This is an optional network boundary. No error text is sent or printed.
        pass


def record(command: str, backend: str | None, outcome: Outcome, seconds: float) -> None:
    """Bound the caller's wait even if DNS resolution ignores socket timeouts."""
    try:
        if forced_off() or not enabled() or command not in COMMANDS:
            return
        target = destination()
        if target is None:
            return
        host, token = target
        safe_backend = backend if backend in VALID_BACKENDS else None
        body = _payload(command, safe_backend, outcome, seconds, token)
        worker = threading.Thread(target=_post, args=(host, body), daemon=True)
        worker.start()
        worker.join(DELIVERY_BUDGET_SECONDS)
    except Exception:
        # Optional config, metadata and thread failures cannot replace a CLI result.
        pass


def _outcome(code: int) -> Outcome:
    return {
        0: "success",
        1: "bad_input",
        2: "cannot_run",
        3: "bar_not_met",
        130: "interrupted",
    }.get(code, "error")


class TelemetryCommand(TyperCommand):
    """Observe parsed commands, preserving Click's return and exception behavior."""

    def invoke(self, ctx: typer.Context) -> object:
        started = time.monotonic()
        outcome: Outcome = "error"
        try:
            result = super().invoke(ctx)
            outcome = "success"
            return result
        except (typer.Exit, click.exceptions.Exit) as exc:
            outcome = _outcome(exc.exit_code)
            raise
        except SystemExit as exc:
            outcome = _outcome(
                exc.code if isinstance(exc.code, int) else 0 if exc.code is None else -1
            )
            raise
        except (KeyboardInterrupt, typer.Abort, click.Abort):
            outcome = "interrupted"
            raise
        finally:
            backend = None
            if self.name == "run":
                backend = (
                    parse_target(ctx.params.get("model") or "")[0] or DEFAULT_BACKEND
                )
            record(self.name, backend, outcome, time.monotonic() - started)
