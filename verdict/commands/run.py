from __future__ import annotations

from pathlib import Path
from typing import Annotated, Optional

import typer
from rich.console import Console

from verdict.harness import get_harness
from verdict.judge import get_judge
from verdict.reporter import (
    make_progress,
    print_banner,
    print_results,
    save_results,
    update_progress,
)
from verdict.runner import TaskRunner
from verdict.schema.spec import load_spec, spec_name

app = typer.Typer(help="Run an evaluation spec")
console = Console()


@app.callback(invoke_without_command=True)
def run_cmd(
    spec_file: Annotated[Path, typer.Argument(help="Path to .eval.yaml spec file")],
    k: Annotated[int, typer.Option("--k", help="Attempts per task")] = 3,
    workers: Annotated[int, typer.Option("--workers", help="Parallel task workers")] = 4,
    timeout: Annotated[int, typer.Option("--timeout", help="Seconds per attempt")] = 120,
    baseline: Annotated[bool, typer.Option("--baseline", help="Also run without skill for delta")] = False,
    judge_strategy: Annotated[str, typer.Option("--judge", help="Judge strategy: autorater | script")] = "autorater",
    output: Annotated[Optional[Path], typer.Option("--output", help="Save results JSON to path")] = None,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Show per-attempt reasoning")] = False,
) -> None:
    if not spec_file.exists():
        console.print(f"[bold red]Error:[/bold red] File not found: {spec_file}")
        raise typer.Exit(1)

    try:
        spec = load_spec(spec_file)
    except Exception as exc:
        console.print(f"[bold red]Invalid spec:[/bold red] {exc}")
        raise typer.Exit(1)

    name = spec_name(spec_file)
    print_banner(name, k, spec.skill.backend)

    harness = get_harness(spec.skill.backend, spec.skill.model)
    judge = get_judge(judge_strategy, spec.judge)

    task_names = [t.name for t in spec.tasks]
    progress, task_ids = make_progress(task_names)

    attempt_counts: dict[str, int] = {t.name: 0 for t in spec.tasks}
    pass_counts: dict[str, int] = {t.name: 0 for t in spec.tasks}

    def on_attempt_done(task_id: str, attempt: int, passed: bool, cheated: bool) -> None:
        task = next((t for t in spec.tasks if t.id == task_id), None)
        if task is None:
            return
        attempt_counts[task.name] += 1
        if passed:
            pass_counts[task.name] += 1
        update_progress(
            progress,
            task_ids,
            task.name,
            k,
            attempt_counts[task.name],
            pass_counts[task.name],
            cheated=cheated,
        )

    runner = TaskRunner(
        harness=harness,
        judge=judge,
        spec=spec,
        spec_path=spec_file,
        k=k,
        workers=workers,
        timeout=timeout,
        baseline=baseline,
        judge_strategy=judge_strategy,
        on_attempt_done=on_attempt_done,
    )

    with progress:
        results = runner.run()

    saved_path = save_results(results, str(spec_file))
    if output:
        Path(output).write_text(results.model_dump_json(indent=2))

    print_results(results, verbose=verbose)
    console.print(f"[dim]Results saved to {saved_path}[/dim]")
