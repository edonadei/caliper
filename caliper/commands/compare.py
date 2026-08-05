from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from caliper.commands._addressing import resolve_run_path
from caliper.compare import IncomparableRunsError, diff_runs
from caliper.reporter import comparison_to_json, print_comparison
from caliper.schema.results import RunResults

console = Console()


def _resolve(ref: str) -> Path:
    path = resolve_run_path(ref)
    if path is None:
        console.print(f"[bold red]Error:[/bold red] No results found for {ref!r}")
        raise typer.Exit(1)
    return path


def _load_run(ref: str, path: Path) -> RunResults:
    try:
        return RunResults.model_validate_json(path.read_text())
    except Exception as exc:
        console.print(f"[bold red]Error parsing results ({ref}):[/bold red] {exc}")
        raise typer.Exit(1)


def _refuse_self_diff(a: str, a_path: Path, b: str, b_path: Path) -> None:
    """Refuse two references that name the same saved run.

    ``compare`` addresses runs but cannot *qualify* them: a bare spec name always
    means that spec's latest run, and there is no per-side ``--run`` (one option
    could not say which of two positionals it applied to). So naming one spec
    twice resolves to a single file — and an ablation control arm now lives in
    the same folder, under the same spec name, as the full run it exists to be
    diffed against, which makes that an easy slip.

    A run diffed against itself renders a clean table of zero deltas with no
    regression and no warning: every guard compares the run to itself and finds
    it consistent. That is the "meaningless but invites no suspicion" shape
    docs/adr/0014 reserves a hard stop for.
    """
    if a_path.resolve() != b_path.resolve():
        return
    console.print(
        f"[bold red]Refusing to compare:[/bold red] {a!r} and {b!r} are the same "
        f"run ({a_path.stem}).\n\n"
        "A bare spec name always resolves to that spec's latest run, so naming "
        "one twice diffs a run against itself — every delta zero, nothing "
        "flagged. Name two distinct runs; address the older side by its path:\n"
        f"  caliper compare .caliper/results/<spec>/<timestamp>.json {b}"
    )
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
) -> None:
    a_path, b_path = _resolve(a), _resolve(b)
    _refuse_self_diff(a, a_path, b, b_path)
    try:
        comparison = diff_runs(_load_run(a, a_path), _load_run(b, b_path))
    except IncomparableRunsError as exc:
        # A hard stop, unlike the k/spec/neighbourhood warnings: a cross-era diff
        # looks entirely normal and would be believed (docs/adr/0013).
        console.print(f"[bold red]Refusing to compare:[/bold red] {exc}")
        raise typer.Exit(1)

    if fmt == "json":
        console.print_json(comparison_to_json(comparison))
    else:
        print_comparison(comparison, verbose=verbose)
