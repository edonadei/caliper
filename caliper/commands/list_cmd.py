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
    spec: Annotated[
        Optional[str], typer.Argument(help="Spec name to list runs for")
    ] = None,
    directory: Annotated[
        Path, typer.Option("--dir", help="Directory to search")
    ] = Path("."),
) -> None:
    caliper_dir = directory / ".caliper" / "results"

    if spec:
        _list_runs(caliper_dir / spec, spec)
    else:
        _list_specs(caliper_dir)


def _list_specs(results_dir: Path) -> None:
    if not results_dir.exists():
        console.print(
            "[dim]No evaluation results found. Run [bold]caliper run[/bold] first.[/dim]"
        )
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
            score = f"{results.aggregate.avg_score * 100:.1f}%"
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
    # No separate timestamp column: the run id *is* its timestamp, and spending
    # a column to restate it in another format is what squeezed the id itself
    # into an ellipsis.
    table.add_column("k", justify="right")
    table.add_column("Tasks", justify="right")
    table.add_column("pass@k", justify="right")
    # Which run was a control arm. Without it two runs of one spec are
    # indistinguishable here, and `compare` needs the older side named by path
    # (docs/CONTEXT.md → Run comparison) — so this is where you find out which
    # file that is, short of opening each one.
    table.add_column("ablated", style="yellow")
    # Folded, never ellipsized: this cell is the handle a caller passes to
    # `compare`/`report --run`, so a truncated one is useless. Wrapping keeps
    # every character on screen at any terminal width.
    table.add_column("Run", style="dim", overflow="fold")

    for f in files:
        ablated = ""
        try:
            results = RunResults.model_validate_json(f.read_text())
            score = f"{results.aggregate.avg_score * 100:.1f}%"
            k = str(results.run.k)
            n_tasks = str(len(results.task_results))
            ablated = ", ".join(results.run.ablated)
        except Exception:
            score = k = n_tasks = "?"

        # The stem, not the filename: it is what `report --run` takes verbatim,
        # and what a `compare` path is built from.
        table.add_row(k, n_tasks, score, ablated, f.stem)

    console.print(table)
