from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Optional

import typer
from rich.console import Console
from rich.table import Table

console = Console()


@dataclass(frozen=True)
class CliTarget:
    name: str
    binary: str
    npm_package: str
    app_bundle: Path | None = None


CODEX_APP_CLI = Path("/Applications/Codex.app/Contents/Resources/codex")

TARGETS = {
    "codex": CliTarget(
        name="codex",
        binary="codex",
        npm_package="@openai/codex",
        app_bundle=CODEX_APP_CLI,
    ),
    "claude-code": CliTarget(
        name="claude-code",
        binary="claude",
        npm_package="@anthropic-ai/claude-code",
    ),
    "pi": CliTarget(
        name="pi",
        binary="pi",
        npm_package="@earendil-works/pi-coding-agent",
    ),
}

ALIASES = {
    "claude": "claude-code",
    "claude_code": "claude-code",
}


def update_cli_cmd(
    target: Annotated[
        Optional[str],
        typer.Argument(help="CLI to update: codex, claude-code, pi, or all"),
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
        _update(cli, yes=yes)


def _resolve_targets(target: str | None) -> list[CliTarget]:
    if target is None or target == "all":
        return list(TARGETS.values())

    normalized = ALIASES.get(target, target)
    cli = TARGETS.get(normalized)
    if cli is None:
        valid = ", ".join([*TARGETS.keys(), "all"])
        raise typer.BadParameter(f"unsupported CLI {target!r}. Choose one of: {valid}")
    return [cli]


def _print_checks(targets: list[CliTarget]) -> None:
    table = Table(header_style="bold cyan", expand=False)
    table.add_column("CLI")
    table.add_column("Used by Caliper")
    table.add_column("Current")
    table.add_column("Latest npm")
    table.add_column("Update")

    for cli in targets:
        command = _command_for(cli)
        current = _current_version(command) if command else "not found"
        latest = _latest_npm_version(cli.npm_package) or "unknown"
        update = _update_hint(cli, command)
        table.add_row(cli.name, command or "-", current, latest, update)

    console.print(table)


def _update(cli: CliTarget, *, yes: bool) -> None:
    command = _command_for(cli)
    app_bundle = _app_bundle_for(cli)
    if app_bundle and command == str(app_bundle) and not os.environ.get("CODEX_CLI_PATH"):
        console.print(
            "[bold yellow]Codex app bundle detected.[/bold yellow] "
            "Caliper currently uses the Codex CLI inside the desktop app, so "
            "`npm install -g @openai/codex` would not update the binary Caliper runs. "
            "Update the Codex app, or set CODEX_CLI_PATH to an npm-installed Codex CLI."
        )
        raise typer.Exit(1)

    npm = shutil.which("npm")
    if not npm:
        console.print("[bold red]Error:[/bold red] npm is required to update agent CLIs.")
        raise typer.Exit(1)

    install_cmd = [npm, "install", "-g", cli.npm_package]
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


def _command_for(cli: CliTarget) -> str | None:
    if cli.name == "codex":
        configured = os.environ.get("CODEX_CLI_PATH")
        if configured and Path(configured).exists():
            return configured
        app_bundle = _app_bundle_for(cli)
        if app_bundle and app_bundle.exists():
            return str(app_bundle)
    return shutil.which(cli.binary)


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


def _update_hint(cli: CliTarget, command: str | None) -> str:
    app_bundle = _app_bundle_for(cli)
    if app_bundle and command == str(app_bundle) and not os.environ.get("CODEX_CLI_PATH"):
        return "update desktop app or set CODEX_CLI_PATH"
    return f"caliper update-cli {cli.name}"


def _app_bundle_for(cli: CliTarget) -> Path | None:
    if cli.name == "codex":
        return CODEX_APP_CLI
    return cli.app_bundle
