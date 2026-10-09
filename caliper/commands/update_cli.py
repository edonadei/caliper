from __future__ import annotations

import os
import re
import shutil
import subprocess
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from caliper.backends import BACKENDS, Backend, Npm, SelfUpdate, normalize_backend
from caliper.harness import get_harness

console = Console()


def update_cli_cmd(
    target: Annotated[
        str | None,
        typer.Argument(help="CLI to update: claude-code, codex, hermes, pi, or all"),
    ] = None,
    check: Annotated[
        bool,
        typer.Option("--check", help="Only print installed and latest npm versions"),
    ] = False,
    yes: Annotated[
        bool,
        typer.Option("--yes", "-y", help="Skip confirmation before updating"),
    ] = False,
) -> None:
    targets = _resolve_targets(target)

    if check:
        _print_checks(targets)
        return

    if target is None:
        console.print(
            "[bold red]Error:[/bold red] choose a CLI to update, "
            "for example [bold]caliper update-cli codex[/bold]."
        )
        raise typer.Exit(1)

    for cli in targets:
        if isinstance(cli.updater, SelfUpdate):
            console.print(
                f"{cli.name} updates itself. Run [bold]{cli.updater.command}[/bold] "
                "to update it."
            )
            if target != "all":
                raise typer.Exit(1)
            continue
        _update(cli, cli.updater, yes=yes)


def _resolve_targets(target: str | None) -> list[Backend]:
    if target is None or target == "all":
        return list(BACKENDS.values())

    cli = BACKENDS.get(normalize_backend(target))
    if cli is None:
        valid = ", ".join([*BACKENDS, "all"])
        raise typer.BadParameter(f"unsupported CLI {target!r}. Choose one of: {valid}")
    return [cli]


def _print_checks(targets: list[Backend]) -> None:
    table = Table(header_style="bold cyan", expand=False)
    table.add_column("CLI")
    table.add_column("Used by Caliper")
    table.add_column("Current")
    table.add_column("Latest npm")
    table.add_column("Update")

    for cli in targets:
        command = _command_for(cli)
        current = _current_version(command) if command else "not found"
        latest = (
            (_latest_npm_version(cli.updater.package) or "unknown")
            if isinstance(cli.updater, Npm)
            else "-"
        )
        update = (
            "up to date"
            if _same_version(current, latest)
            else _update_hint(cli, command)
        )
        table.add_row(cli.name, command or "-", current, latest, update)

    console.print(table)


def _update(cli: Backend, updater: Npm, *, yes: bool) -> None:
    command = _command_for(cli)
    env_var = _app_bundle_env_var(cli, command)
    if env_var:
        console.print(
            "[bold yellow]App bundle detected.[/bold yellow] "
            f"Caliper uses the {cli.name} CLI inside the desktop app, so "
            f"`npm install -g {updater.package}` would not update the binary "
            f"Caliper runs. Update the app, or set {env_var} to an "
            "npm-installed CLI."
        )
        raise typer.Exit(1)

    npm = shutil.which("npm")
    if not npm:
        console.print(
            "[bold red]Error:[/bold red] npm is required to update agent CLIs."
        )
        raise typer.Exit(1)

    install_cmd = [npm, "install", "-g", updater.package]
    if not yes:
        confirmed = typer.confirm(
            f"Run {' '.join(install_cmd)} to update {cli.name}?",
            default=False,
        )
        if not confirmed:
            raise typer.Exit(1)

    console.print(f"[bold]Updating {cli.name}[/bold] with npm...")
    proc = subprocess.run(install_cmd, text=True)
    if proc.returncode != 0:
        raise typer.Exit(proc.returncode)

    updated_command = _command_for(cli)
    version = _current_version(updated_command) if updated_command else "unknown"
    console.print(f"[bold green]Updated {cli.name}[/bold green] ({version})")


def _command_for(cli: Backend) -> str | None:
    """The CLI caliper itself would run: the harness's own lookup."""
    return get_harness(cli.name).cli_path()


def _current_version(command: str | None) -> str:
    if not command:
        return "not found"
    try:
        proc = subprocess.run(
            [command, "--version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"

    text = (proc.stdout or proc.stderr).strip()
    return text.splitlines()[0] if text else "unknown"


def _latest_npm_version(package: str) -> str | None:
    npm = shutil.which("npm")
    if not npm:
        return None
    try:
        proc = subprocess.run(
            [npm, "view", package, "version"],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else None


_VERSION_RE = re.compile(r"\d+\.\d+\.\d+\S*")


def _same_version(current: str, latest: str) -> bool:
    """Whether ``current`` (a CLI's ``--version`` line) is the ``latest`` release.

    The CLI decorates its version (``2.1.281 (Claude Code)``, ``codex-cli
    0.156.1``), so the first version-shaped token is compared.
    """
    found = _VERSION_RE.search(current)
    return found is not None and found.group() == latest.strip()


def _update_hint(cli: Backend, command: str | None) -> str:
    if isinstance(cli.updater, SelfUpdate):
        return cli.updater.command
    env_var = _app_bundle_env_var(cli, command)
    if env_var:
        return f"update desktop app or set {env_var}"
    return f"caliper update-cli {cli.name}"


def _app_bundle_env_var(cli: Backend, command: str | None) -> str | None:
    """The override to suggest when caliper runs a CLI bundled in an app.

    npm cannot update a bundled CLI. ``None`` when ``command`` is not one of the
    harness's install candidates, or an override already points elsewhere.
    """
    harness = get_harness(cli.name)
    bundled = {str(path) for path in harness.cli_candidates()}
    env_var = harness.cli_path_env_var
    if command not in bundled or (env_var and os.environ.get(env_var)):
        return None
    return env_var
