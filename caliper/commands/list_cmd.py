from __future__ import annotations

from pathlib import Path
from typing import Annotated, Optional

import typer
from rich import box
from rich.console import Console
from rich.table import Table

from caliper.reporter import UNUSABLE_GLYPH
from caliper.schema.results import RunResults

console = Console()

# Marks a run that stopped before every attempt ran. Attached to the *score*
# rather than given a column of its own: the score is the cell a reader would
# otherwise take at face value, and the runs listing is already at its width
# budget (the Run id is folded, not ellipsized, for exactly that reason).
#
# Shares the reporter's glyph rather than hardcoding one, so it degrades on a
# non-UTF-8 terminal exactly like every other marker caliper prints.
_INTERRUPTED = UNUSABLE_GLYPH


def _score_cell(results: RunResults) -> str:
    score = f"{results.aggregate.avg_score * 100:.1f}%"
    if results.run.interrupted:
        return f"{score} [yellow]{_INTERRUPTED}[/yellow]"
    return score


def _print_interrupted_legend(marked: bool) -> None:
    """Explain the marker, only when one is on screen."""
    if marked:
        console.print(
            f" [dim][yellow]{_INTERRUPTED}[/yellow] stopped early: scored over "
            "fewer attempts than its k[/dim]"
        )


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

    marked = False
    for spec_dir in sorted(results_dir.iterdir()):
        if not spec_dir.is_dir():
            continue
        files = sorted(spec_dir.glob("*.json"))
        if not files:
            continue
        latest_file = files[-1]
        interrupted = False
        try:
            results = RunResults.model_validate_json(latest_file.read_text())
            ts = results.run.timestamp.strftime("%Y-%m-%d %H:%M")
            score = _score_cell(results)
            interrupted = results.run.interrupted
        except Exception:
            ts = latest_file.stem
            score = "?"
        marked = marked or interrupted

        table.add_row(spec_dir.name, str(len(files)), ts, score)

    if table.row_count == 0:
        console.print("[dim]No results yet.[/dim]")
    else:
        console.print(table)
        _print_interrupted_legend(marked)


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

    marked = False
    for f in files:
        ablated = ""
        try:
            results = RunResults.model_validate_json(f.read_text())
            score = _score_cell(results)
            k = str(results.run.k)
            n_tasks = str(len(results.task_results))
            ablated = ", ".join(results.run.ablated)
            marked = marked or results.run.interrupted
        except Exception:
            score = k = n_tasks = "?"

        # The stem, not the filename: it is what `report --run` takes verbatim,
        # and what a `compare` path is built from.
        table.add_row(k, n_tasks, score, ablated, f.stem)

    console.print(table)
    _print_interrupted_legend(marked)
