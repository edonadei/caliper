"""Opt-out command counts. Only the fields constructed here leave the machine.

Collection starts only after the first-run notice has been shown, and never in
CI unless enabled explicitly (docs/adr/0042-usage-telemetry-is-opt-out-after-a-notice.md).
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
from caliper.backends import VALID_BACKENDS
from caliper.commands.diagnosis import CannotRun, ExitCode

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


NOTICE = (
    "Caliper collects anonymous usage metrics: the command, Caliper version, "
    "OS family, backend, outcome and a duration bucket. No prompts, outputs, "
    "paths, skill or model names, arguments or IDs are sent. Nothing was sent "
    "for this command.\n"
    "Opt out with `caliper telemetry disable` or CALIPER_TELEMETRY_DISABLED=1. "
    "Details: https://github.com/edonadei/caliper/blob/main/docs/telemetry.md"
)


def preference() -> bool | None:
    """The saved choice, or None before one exists (the notice is still due).

    A malformed or unreadable preference means no collection: it is never
    replaced by the default.
    """
    try:
        text = (config.config_home() / "telemetry.json").read_text()
    except FileNotFoundError:
        return None
    except (OSError, RuntimeError):
        return False
    try:
        consent = json.loads(text)
    except ValueError:
        return False
    return isinstance(consent, dict) and consent.get("enabled") is True


def enabled() -> bool:
    """Whether a saved preference allows collection. No preference yet is not one."""
    return preference() is True


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


def _set(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() not in {"", "0", "false", "no"}


def forced_off() -> bool:
    """CALIPER_TELEMETRY_DISABLED or the cross-tool DO_NOT_TRACK convention."""
    return bool(os.environ.get("CALIPER_TELEMETRY_DISABLED")) or _set("DO_NOT_TRACK")


def in_ci() -> bool:
    """Nobody reads a first-run notice in CI, so CI never defaults in."""
    return _set("CI")


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


def _first_run_notice() -> None:
    """Show the notice, then save the default. Nothing is sent on this run.

    Printing first means a failed save repeats the notice rather than enrolling
    someone who never saw it.
    """
    typer.echo(NOTICE, err=True)
    set_enabled(True)


def _backend(model: str | None) -> str | None:
    """The backend ``caliper run`` selected, by the same rule it uses."""
    from caliper.commands.engine import resolve_engine

    try:
        backend = resolve_engine(model, None).backend
    except CannotRun:
        return None
    return backend if backend in VALID_BACKENDS else None


def record(
    command: str, outcome: Outcome, seconds: float, model: str | None = None
) -> None:
    """Bound the caller's wait even if DNS resolution ignores socket timeouts."""
    try:
        if command not in COMMANDS or forced_off():
            return
        saved = preference()
        if saved is None and not in_ci():
            _first_run_notice()
        if saved is not True:
            return
        target = destination()
        if target is None:
            return
        host, token = target
        backend = _backend(model) if command == "run" else None
        body = _payload(command, backend, outcome, seconds, token)
        worker = threading.Thread(target=_post, args=(host, body), daemon=True)
        worker.start()
        if outcome != "interrupted":
            # Someone who pressed Ctrl-C asked to leave; don't hold them up.
            worker.join(DELIVERY_BUDGET_SECONDS)
    except (Exception, KeyboardInterrupt):
        # Optional config, metadata and thread failures cannot replace a CLI
        # result, and neither can a second Ctrl-C during the bounded wait.
        pass


_OUTCOMES: dict[int, Outcome] = {
    ExitCode.OK: "success",
    ExitCode.BAD_INPUT: "bad_input",
    ExitCode.CANNOT_RUN: "cannot_run",
    ExitCode.BAR_NOT_MET: "bar_not_met",
    ExitCode.INTERRUPTED: "interrupted",
}


def _outcome(code: int) -> Outcome:
    return _OUTCOMES.get(code, "error")


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
            record(
                self.name,
                outcome,
                time.monotonic() - started,
                model=ctx.params.get("model"),
            )
