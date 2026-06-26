from __future__ import annotations

from pathlib import Path
from typing import Annotated, Optional

import typer

from caliper.wizard import run_wizard

def new_cmd(
    name: Annotated[Optional[str], typer.Argument(help="Eval name (used as default filename)")] = None,
    skill: Annotated[Optional[str], typer.Option("--skill", help="Pre-populate skill path")] = None,
    backend: Annotated[str, typer.Option("--backend", help="Pre-populate backend (claude-code, codex, claude-api, openai-api, pi)")] = "claude-code",
    output: Annotated[Optional[Path], typer.Option("--out", help="Output path for .eval.yaml")] = None,
) -> None:
    run_wizard(name=name, output=output, skill_path=skill, backend=backend)
