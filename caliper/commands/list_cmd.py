from __future__ import annotations

from pathlib import Path
from typing import Annotated, Optional

import typer
from rich import box
from rich.console import Console
from rich.table import Table

from caliper.commands.diagnosis import BadInput, fail
from caliper.reporter import RULE_GLYPH, UNUSABLE_GLYPH
from caliper.runstore import RunStore, UnreadableRun
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
    """The run's headline, on the same rule the report renders it by.

    A run whose every task is a trigger probe measured no execution question, so
    it has no headline — and printing ``avg_score`` regardless is what made the
    listing disagree with the report about the same file, calling "nothing was
    asked" a total failure.
    """
    if not results.aggregate.measured:
        return f"[dim]{RULE_GLYPH}[/dim]"
    score = f"{results.aggregate.avg_score * 100:.1f}%"
    if results.run.interrupted:
        return f"{score} [yellow]{_INTERRUPTED}[/yellow]"
    return score


def _print_legend(*, interrupted: bool, unmeasured: bool) -> None:
    """Explain the markers, only the ones on screen."""
    if interrupted:
        console.print(
            f" [dim][yellow]{_INTERRUPTED}[/yellow] stopped early: scored over "
            "fewer attempts than its k[/dim]"
        )
    if unmeasured:
        console.print(f" [dim]{RULE_GLYPH} no execution checks[/dim]")


def _read(store: RunStore, path: Path) -> RunResults | None:
    """One run, or ``None`` with a warning when the file will not parse.

    The listing is the one command that must survive a corrupt run: it renders a
    row per file, and refusing the whole table over one bad one would hide every
    good run beside it. So the row degrades to "?" — but the file is named, which
    a bare ``except`` never did.
    """
    try:
        return store.load(path)
    except UnreadableRun as exc:
        console.print(f"[yellow]Skipping unreadable run:[/yellow] [dim]{exc}[/dim]")
        return None


def list_cmd_fn(
    spec: Annotated[
        Optional[str], typer.Argument(help="Spec name to list runs for")
    ] = None,
) -> None:
    # No `--dir`: the results root is discovered, the same way for every command
    # (docs/adr/0022-saved-runs-live-at-a-discovered-results-root.md). A flag
    # that pointed the listing at a root `report` and `compare` could not follow
    # was the asymmetry discovery removes. To read another project, work from it.
    store = RunStore.discover()

    if spec:
        _list_runs(store, spec)
    else:
        _list_specs(store)


def _list_specs(store: RunStore) -> None:
    specs = store.specs()
    if not specs:
        console.print(
            f"[dim]No evaluation results under {store.root}. "
            "Run [bold]caliper run[/bold] first.[/dim]"
        )
        return

    table = Table(box=box.ROUNDED, header_style="bold cyan", expand=False)
    table.add_column("Spec")
    table.add_column("Runs", justify="right")
    table.add_column("Latest run")
    # The raw success rate, as the report heads it (docs/adr/0007). The cell has
    # always held ``avg_score``; only the label was left behind by the rename.
    table.add_column("success", justify="right")

    marked = unmeasured = False
    for name in specs:
        runs = store.runs(name)
        latest = runs[-1]
        results = _read(store, latest)
        if results is None:
            # The run id is the timestamp, so a file we cannot parse still knows
            # when it ran — that much is in its name.
            ts, score = latest.stem, "?"
        else:
            ts = results.run.timestamp.strftime("%Y-%m-%d %H:%M")
            score = _score_cell(results)
            marked = marked or results.run.interrupted
            unmeasured = unmeasured or not results.aggregate.measured

        table.add_row(name, str(len(runs)), ts, score)

    console.print(table)
    _print_legend(interrupted=marked, unmeasured=unmeasured)


def _list_runs(store: RunStore, spec_name: str) -> None:
    if not store.has_spec(spec_name):
        fail(BadInput(store.no_results(spec_name)))

    runs = store.runs(spec_name)
    if not runs:
        console.print(f"[dim]No runs for {spec_name}.[/dim]")
        return

    table = Table(box=box.ROUNDED, header_style="bold cyan", expand=False)
    # No separate timestamp column: the run id *is* its timestamp, and spending
    # a column to restate it in another format is what squeezed the id itself
    # into an ellipsis.
    table.add_column("k", justify="right")
    table.add_column("Tasks", justify="right")
    table.add_column("success", justify="right")
    # Which run was a control arm. Without it two runs of one spec are
    # indistinguishable here, and `compare` needs the older side named by path
    # (docs/CONTEXT.md → Run comparison) — so this is where you find out which
    # file that is, short of opening each one.
    table.add_column("ablated", style="yellow")
    # Folded, never ellipsized: this cell is the handle a caller passes to
    # `compare`/`report --run`, so a truncated one is useless. Wrapping keeps
    # every character on screen at any terminal width.
    table.add_column("Run", style="dim", overflow="fold")

    marked = unmeasured = False
    for path in runs:
        results = _read(store, path)
        if results is None:
            score = k = n_tasks = "?"
            ablated = ""
        else:
            score = _score_cell(results)
            k = str(results.run.k)
            n_tasks = str(len(results.task_results))
            ablated = ", ".join(results.run.ablated)
            marked = marked or results.run.interrupted
            unmeasured = unmeasured or not results.aggregate.measured

        # The stem, not the filename: it is what `report --run` takes verbatim,
        # and what a `compare` path is built from.
        table.add_row(k, n_tasks, score, ablated, path.stem)

    console.print(table)
    _print_legend(interrupted=marked, unmeasured=unmeasured)
