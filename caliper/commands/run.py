from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel

from caliper.harness.base import HarnessConfigurationError
from caliper.harness import get_harness
from caliper.judge import get_judge
from caliper.reporter import (
    make_progress,
    print_banner,
    print_results,
    save_results,
    update_progress,
)
from caliper.runner import run, AttemptEvent
from caliper.schema.spec import load_spec, spec_name

console = Console()


def run_cmd(
    spec_file: Path = typer.Argument(..., help="Path to .eval.yaml spec file"),
    k: int = typer.Option(3, "--k", help="Attempts per task"),
    workers: int = typer.Option(4, "--workers", help="Parallel task workers"),
    timeout: int = typer.Option(120, "--timeout", help="Seconds per attempt"),
    baseline: bool = typer.Option(False, "--baseline", help="Also run without skill for delta"),
    judge_strategy: str = typer.Option("autorater", "--judge", help="Judge strategy: autorater | script"),
    output: Optional[Path] = typer.Option(None, "--output", help="Save results JSON to path"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show per-attempt reasoning"),
    model: Optional[str] = typer.Option(None, "--model", "-m", help="Override the skill model (e.g. claude-sonnet-4-6)"),
) -> None:
    if not spec_file.exists():
        console.print(f"[bold red]Error:[/bold red] File not found: {spec_file}")
        raise typer.Exit(1)

    try:
        spec = load_spec(spec_file)
    except Exception as exc:
        console.print(f"[bold red]Invalid spec:[/bold red] {exc}")
        raise typer.Exit(1)

    if model:
        spec.skill.model = model

    name = spec_name(spec_file)
    print_banner(name, k, spec.skill.backend)

    harness = get_harness(spec.skill.backend, spec.skill.model)
    judge = get_judge(judge_strategy, spec.judge)

    task_names = [t.name for t in spec.tasks]
    progress, task_ids = make_progress(task_names, k)

    attempt_counts: dict[str, int] = {t.name: 0 for t in spec.tasks}
    pass_counts: dict[str, int] = {t.name: 0 for t in spec.tasks}

    def on_attempt_done(event: AttemptEvent) -> None:
        task = next((t for t in spec.tasks if t.id == event.task_id), None)
        if task is None:
            return
        attempt_counts[task.name] += 1
        if event.passed:
            pass_counts[task.name] += 1
        update_progress(
            progress,
            task_ids,
            task.name,
            k,
            attempt_counts[task.name],
            pass_counts[task.name],
            cheated=event.cheated,
        )

    with progress:
        try:
            results = run(
                spec=spec,
                spec_path=spec_file,
                harness=harness,
                judge=judge,
                k=k,
                workers=workers,
                timeout=timeout,
                baseline=baseline,
                on_attempt_done=on_attempt_done,
            )
        except HarnessConfigurationError as exc:
            console.print(
                Panel(
                    str(exc),
                    title="[bold red]Backend configuration error[/bold red]",
                    border_style="red",
                )
            )
            raise typer.Exit(2)

    saved_path = save_results(results, str(spec_file))
    if output:
        Path(output).write_text(results.model_dump_json(indent=2))

    print_results(results, verbose=verbose)
    console.print(f"[dim]Results saved to {saved_path}[/dim]")
