from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from caliper.commands.diagnosis import BadInput, fail
from caliper.compare import IncomparableRunsError, diff_runs
from caliper.reporter import comparison_to_json, print_comparison
from caliper.runstore import RunStore, UnreadableRun
from caliper.schema.results import RunResults

console = Console()

_STORE = RunStore()


def _resolve(ref: str) -> Path:
    path = _STORE.resolve(ref)
    if path is None:
        fail(BadInput(f"No results found for {ref!r}"))
    return path


def _load_run(path: Path) -> RunResults:
    """One saved run, or the diagnosis for a file that will not parse.

    The reference the user typed is not carried in: ``UnreadableRun`` names the
    file itself, which is what a reader has to go and look at.
    """
    try:
        return _STORE.load(path)
    except UnreadableRun as exc:
        fail(exc)


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
    fail(
        BadInput(
            f"{a!r} and {b!r} are the same run ({a_path.stem}).\n\n"
            "A bare spec name always resolves to that spec's latest run, so "
            "naming one twice diffs a run against itself — every delta zero, "
            "nothing flagged. Name two distinct runs; address the older side by "
            "its path:\n"
            f"  caliper compare {_STORE.spec_dir('<spec>') / '<timestamp>.json'} {b}",
            title="Refusing to compare",
        )
    )


# Deliberately no gate flag and no verdict exit code: `has_regression` fires on
# the any-below rule (docs/CONTEXT.md → Regression), which at small k is noise as
# often as signal, so it is not a thing to fail a pipeline on. Gating belongs on
# `run` against a *pre-registered* bar; exit 3 is reserved for it in the README's
# exit-code contract.
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
    # Loaded before the try, not inside it: ``_load_run`` exits through ``fail``,
    # and ``typer.Exit`` is a RuntimeError — so a load that failed here would be
    # caught by any except clause wide enough to name one.
    run_a, run_b = _load_run(a_path), _load_run(b_path)
    try:
        comparison = diff_runs(run_a, run_b)
    except IncomparableRunsError as exc:
        fail(exc)

    if fmt == "json":
        console.print_json(comparison_to_json(comparison))
    else:
        print_comparison(comparison, verbose=verbose)
