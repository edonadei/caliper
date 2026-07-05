from __future__ import annotations

from typing import Annotated, Optional

import typer
from rich.console import Console

from caliper.commands._addressing import resolve_run_path
from caliper.reporter import print_results
from caliper.schema.results import RunResults, UsageTotals

console = Console()


def report_cmd(
    spec_or_file: Annotated[
        str, typer.Argument(help="Spec name or path to results JSON")
    ],
    run: Annotated[
        Optional[str], typer.Option("--run", help="Specific run timestamp")
    ] = None,
    fmt: Annotated[
        str, typer.Option("--format", "-f", help="Output format: table | json")
    ] = "table",
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False,
) -> None:
    results_path = resolve_run_path(spec_or_file, run)
    if results_path is None:
        console.print(
            f"[bold red]Error:[/bold red] No results found for {spec_or_file!r}"
        )
        raise typer.Exit(1)

    try:
        results = RunResults.model_validate_json(results_path.read_text())
    except Exception as exc:
        console.print(f"[bold red]Error parsing results:[/bold red] {exc}")
        raise typer.Exit(1)

    if fmt == "json":
        # Derive the run usage totals on the fly (never persisted on RunResults —
        # see CONTEXT.md → Run usage totals); the saved file keeps only the raw
        # per-attempt usage.
        data = results.model_dump(mode="json")
        data["usage_totals"] = UsageTotals.from_task_results(
            results.task_results
        ).model_dump(mode="json")
        console.print_json(data=data)
    else:
        print_results(results, verbose=verbose)
