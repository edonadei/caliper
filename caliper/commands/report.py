from __future__ import annotations

from typing import Annotated, Optional

import typer
from rich.console import Console

from caliper.commands.diagnosis import BadInput, fail
from caliper.reporter import print_results
from caliper.runstore import RunStore, UnreadableRun
from caliper.schema.results import UsageTotals

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
    store = RunStore.discover()
    results_path = store.resolve(spec_or_file, run)
    if results_path is None:
        fail(BadInput(store.no_results(spec_or_file)))

    try:
        results = store.load(results_path)
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
