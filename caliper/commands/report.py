from __future__ import annotations

from typing import Annotated, Optional

import typer
from rich.console import Console

from caliper.commands.diagnosis import BadInput, fail
from caliper.reporter import print_results
from caliper.runstore import RunStore, UnreadableRun
from caliper.schema.results import UsageTotals

console = Console()

_STORE = RunStore()


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
    results_path = _STORE.resolve(spec_or_file, run)
    if results_path is None:
        fail(BadInput(f"No results found for {spec_or_file!r}"))

    try:
        results = _STORE.load(results_path)
    except UnreadableRun as exc:
        fail(exc)

    if fmt == "json":
        # Derive the run usage totals on the fly (never persisted on RunResults —
        # see docs/CONTEXT.md → Run usage totals); the saved file keeps only the raw
        # per-attempt usage.
        data = results.model_dump(mode="json")
        data["usage_totals"] = UsageTotals.from_task_results(
            results.task_results
        ).model_dump(mode="json")
        console.print_json(data=data)
    else:
        print_results(results, verbose=verbose)
