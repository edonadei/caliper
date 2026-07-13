from __future__ import annotations

from typing import Annotated

import typer
from rich.console import Console

from caliper.commands._addressing import resolve_run_path
from caliper.compare import diff_runs
from caliper.reporter import comparison_to_json, print_comparison
from caliper.schema.results import RunResults

console = Console()


def _load_run(ref: str) -> RunResults:
    path = resolve_run_path(ref)
    if path is None:
        console.print(f"[bold red]Error:[/bold red] No results found for {ref!r}")
        raise typer.Exit(1)
    try:
        return RunResults.model_validate_json(path.read_text())
    except Exception as exc:
        console.print(f"[bold red]Error parsing results ({ref}):[/bold red] {exc}")
        raise typer.Exit(1)


def compare_cmd(
    a: Annotated[
        str,
        typer.Argument(help="Run A: spec name (latest run) or path to results JSON"),
    ],
    b: Annotated[
        str,
        typer.Argument(help="Run B: spec name (latest run) or path to results JSON"),
    ],
    fmt: Annotated[
        str, typer.Option("--format", "-f", help="Output format: table | json")
    ] = "table",
    verbose: Annotated[
        bool, typer.Option("--verbose", "-v", help="Also show pass@k and pass^k")
    ] = False,
    margin: Annotated[
        float,
        typer.Option(
            "--margin",
            help=(
                "Regression flag fires only when B drops more than this many "
                "percentage points below A (default 0 = any-below)"
            ),
            min=0.0,
        ),
    ] = 0.0,
) -> None:
    comparison = diff_runs(_load_run(a), _load_run(b), margin=margin / 100.0)

    if fmt == "json":
        console.print_json(comparison_to_json(comparison))
    else:
        print_comparison(comparison, verbose=verbose)
