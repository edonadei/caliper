from pathlib import Path

import typer
from pydantic import ValidationError
from rich.console import Console
from rich.panel import Panel
from rich import print as rprint

from verdict.schema.spec import load_spec, spec_name

app = typer.Typer(help="Validate an evaluation spec file")
console = Console()


@app.callback(invoke_without_command=True)
def validate_cmd(
    spec_file: Path = typer.Argument(..., help="Path to .eval.yaml spec file"),
) -> None:
    if not spec_file.exists():
        console.print(f"[bold red]Error:[/bold red] File not found: {spec_file}")
        raise typer.Exit(1)

    try:
        spec = load_spec(spec_file)
    except ValidationError as exc:
        console.print(
            Panel(
                _format_validation_errors(exc),
                title="[bold red]Validation failed[/bold red]",
                border_style="red",
            )
        )
        raise typer.Exit(1)
    except Exception as exc:
        console.print(f"[bold red]Error parsing YAML:[/bold red] {exc}")
        raise typer.Exit(1)

    name = spec_name(spec_file)
    n_tasks = len(spec.tasks)
    backend = spec.skill.backend

    console.print(
        Panel(
            f"[bold]{name}[/bold]\n"
            f"  backend  [cyan]{backend}[/cyan]\n"
            f"  tasks    [cyan]{n_tasks}[/cyan]\n"
            f"  judge    [cyan]{spec.judge.backend}[/cyan]"
            + (f" / [dim]{spec.judge.model}[/dim]" if spec.judge.model else ""),
            title="[bold green]✓ Spec is valid[/bold green]",
            border_style="green",
        )
    )


def _format_validation_errors(exc: ValidationError) -> str:
    lines = []
    for err in exc.errors():
        loc = " → ".join(str(p) for p in err["loc"])
        lines.append(f"  [dim]{loc}[/dim]  {err['msg']}")
    return "\n".join(lines)
