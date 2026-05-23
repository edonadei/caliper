from __future__ import annotations

from importlib.resources import files
from pathlib import Path
from typing import Annotated

import typer
from click import ClickException

SKILL_PACKAGE = "caliper.resources.evaluate_skill"
SKILL_FILENAME = "SKILL.md"

TARGETS = {
    "codex": Path(".codex") / "skills" / "evaluate-skill" / "SKILL.md",
    "claude-code": Path(".claude") / "commands" / "evaluate-skill.md",
}


def load_packaged_skill() -> str:
    return files(SKILL_PACKAGE).joinpath(SKILL_FILENAME).read_text(encoding="utf-8")


def install_skill_cmd(
    target: Annotated[
        str,
        typer.Argument(help="Agent target to install for: codex or claude-code"),
    ],
    force: Annotated[
        bool,
        typer.Option("--force", help="Overwrite an existing installed skill"),
    ] = False,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Show the destination without writing files"),
    ] = False,
) -> None:
    normalized = target.strip().lower()
    if normalized not in TARGETS:
        valid = ", ".join(sorted(TARGETS))
        raise typer.BadParameter(f"unsupported target '{target}'. Choose one of: {valid}")

    destination = Path.home() / TARGETS[normalized]
    skill_text = load_packaged_skill()

    if dry_run:
        typer.echo(f"Would install evaluate-skill for {normalized} to {destination}")
        return

    if destination.exists() and not force:
        raise ClickException(
            f"{destination} already exists. Rerun with --force to overwrite it."
        )

    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(skill_text, encoding="utf-8")
    typer.echo(f"Installed evaluate-skill for {normalized} to {destination}")
