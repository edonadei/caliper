from __future__ import annotations

from enum import Enum
from typing import Annotated

import typer
from rich.console import Console

from caliper.commands.diagnosis import BadInput, fail
from caliper.gate import evaluate
from caliper.markdown import run_markdown
from caliper.reporter import print_results
from caliper.runstore import RunStore, UnreadableRun

console = Console()


class OutputFormat(str, Enum):
    """What ``report`` and ``compare`` can print. A typer choice, so an unknown
    value is refused rather than silently rendered as a table."""

    TABLE = "table"
    JSON = "json"
    # For a pull-request comment or a job summary (caliper/markdown.py).
    MARKDOWN = "markdown"


def report_cmd(
    spec_or_file: Annotated[
        str, typer.Argument(help="Spec name or path to results JSON")
    ],
    run: Annotated[
        str | None, typer.Option("--run", help="Specific run timestamp")
    ] = None,
    fmt: Annotated[
        OutputFormat, typer.Option("--format", "-f", help="Output format")
    ] = OutputFormat.TABLE,
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

    if fmt is OutputFormat.JSON:
        # Derive the run usage totals on the fly (never persisted on RunResults —
        # see docs/CONTEXT.md → Run usage totals); the saved file keeps only the raw
        # per-attempt usage.
        data = results.model_dump(mode="json")
        data["usage_totals"] = results.usage.model_dump(mode="json")
        # Derived the same way: the verdict follows from the recorded bar and
        # the attempts, so it is never stored beside them (docs/adr/0035).
        gate = evaluate(results)
        data["gate"] = gate.to_json() if gate is not None else None
        console.print_json(data=data)
    elif fmt is OutputFormat.MARKDOWN:
        # Plain stdout, not rich: markup in task names must reach the file as typed.
        typer.echo(run_markdown(results), nl=False)
    else:
        print_results(results, verbose=verbose)
