from __future__ import annotations

from pathlib import Path
from typing import Annotated, Optional

import typer
from rich.console import Console

from caliper.reporter import print_results, results_to_json
from caliper.schema.results import RunResults

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
    results_path = _resolve_path(spec_or_file, run)
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
        console.print_json(results_to_json(results))
    else:
        print_results(results, verbose=verbose)


def _resolve_path(spec_or_file: str, run: str | None) -> Path | None:
    p = Path(spec_or_file)

    # Direct JSON path
    if p.suffix == ".json" and p.exists():
        return p

    # Spec name → look in .caliper/results/<name>/
    results_dir = Path(".caliper") / "results" / spec_or_file
    if not results_dir.exists():
        return None

    if run:
        candidate = results_dir / f"{run}.json"
        return candidate if candidate.exists() else None

    # Latest = lexicographically last (ISO timestamps sort correctly)
    files = sorted(results_dir.glob("*.json"))
    return files[-1] if files else None
