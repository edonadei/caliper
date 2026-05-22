from __future__ import annotations

from pathlib import Path
from typing import Annotated, Optional

import typer
from rich import box
from rich.console import Console
from rich.table import Table

from caliper.schema.results import RunResults

console = Console()


def list_cmd_fn(
    spec: Annotated[Optional[str], typer.Argument(help="Spec name to list runs for")] = None,
    directory: Annotated[Path, typer.Option("--dir", help="Directory to search")] = Path("."),
) -> None:
    caliper_dir = directory / ".caliper" / "results"

    if spec:
        _list_runs(caliper_dir / spec, spec)
    else:
        _list_specs(caliper_dir)


def _list_specs(results_dir: Path) -> None:
    if not results_dir.exists():
        console.print("[dim]No evaluation results found. Run [bold]caliper run[/bold] first.[/dim]")
        return

    table = Table(box=box.ROUNDED, header_style="bold cyan", expand=False)
    table.add_column("Spec")
    table.add_column("Runs", justify="right")
    table.add_column("Latest run")
    table.add_column("pass@k", justify="right")

    for spec_dir in sorted(results_dir.iterdir()):
        if not spec_dir.is_dir():
            continue
        files = sorted(spec_dir.glob("*.json"))
        if not files:
            continue
        latest_file = files[-1]
        try:
            results = RunResults.model_validate_json(latest_file.read_text())
            ts = results.run.timestamp.strftime("%Y-%m-%d %H:%M")
            score = f"{results.aggregate.avg_pass_at_k * 100:.1f}%"
        except Exception:
            ts = latest_file.stem
            score = "?"

        table.add_row(spec_dir.name, str(len(files)), ts, score)

    if table.row_count == 0:
        console.print("[dim]No results yet.[/dim]")
    else:
        console.print(table)


def _list_runs(spec_dir: Path, spec_name: str) -> None:
    if not spec_dir.exists():
        console.print(f"[bold red]Error:[/bold red] No results for spec {spec_name!r}")
        raise typer.Exit(1)

    files = sorted(spec_dir.glob("*.json"))
    if not files:
        console.print(f"[dim]No runs for {spec_name}.[/dim]")
        return

    table = Table(box=box.ROUNDED, header_style="bold cyan", expand=False)
    table.add_column("Timestamp")
    table.add_column("k", justify="right")
    table.add_column("Tasks", justify="right")
    table.add_column("pass@k", justify="right")
    table.add_column("Judge")
    table.add_column("File", style="dim")

    for f in files:
        try:
            results = RunResults.model_validate_json(f.read_text())
            ts = results.run.timestamp.strftime("%Y-%m-%d %H:%M:%S")
            score = f"{results.aggregate.avg_pass_at_k * 100:.1f}%"
            k = str(results.run.k)
            n_tasks = str(len(results.task_results))
            judge = results.run.judge_strategy
        except Exception:
            ts = f.stem
            score = k = n_tasks = judge = "?"

        table.add_row(ts, k, n_tasks, score, judge, f.name)

    console.print(table)
