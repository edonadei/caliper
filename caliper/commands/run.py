from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel

from caliper.harness.base import HarnessConfigurationError
from caliper.harness import get_harness
from caliper.judge import EvalJudge
from caliper.reporter import (
    make_progress,
    print_banner,
    print_results,
    save_results,
    update_progress,
)
from caliper.runner import run, AttemptEvent
from caliper.schema.results import Outcome, TaskResult
from caliper.schema.spec import DEFAULT_BACKEND, load_spec, parse_target, spec_name

console = Console()


def run_cmd(
    spec_file: Path = typer.Argument(..., help="Path to .eval.yaml spec file"),
    k: int = typer.Option(3, "--k", help="Attempts per task"),
    workers: int = typer.Option(4, "--workers", help="Parallel task workers"),
    timeout: int = typer.Option(120, "--timeout", help="Seconds per attempt"),
    fail_fast_unusable: int = typer.Option(
        0,
        "--fail-fast",
        min=0,
        help="Stop a task after N consecutive infra_error/timeout attempts (0 disables)",
    ),
    baseline: bool = typer.Option(
        False, "--baseline", help="Also run without skill for delta"
    ),
    output: Optional[Path] = typer.Option(
        None, "--output", help="Save results JSON to path"
    ),
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="Show per-attempt reasoning"
    ),
    model: Optional[str] = typer.Option(
        None,
        "--model",
        "-m",
        help="Override skill backend/model (e.g. codex:gpt-5-codex or claude-sonnet-4-6)",
    ),
    judge_model: Optional[str] = typer.Option(
        None,
        "--judge-model",
        help="Override judge backend/model (e.g. claude-code:claude-haiku-4-5-20251001)",
    ),
) -> None:
    if not spec_file.exists():
        console.print(f"[bold red]Error:[/bold red] File not found: {spec_file}")
        raise typer.Exit(1)

    try:
        spec = load_spec(spec_file)
    except Exception as exc:
        console.print(f"[bold red]Invalid spec:[/bold red] {exc}")
        raise typer.Exit(1)

    # The engine is a runtime axis, not a spec field (ADR 0004): resolve it here
    # from the flags, defaulting to claude-code. The resolved (backend, model)
    # is what gets recorded in RunMeta.
    backend, skill_model = DEFAULT_BACKEND, None
    if model:
        b, m = parse_target(model)
        backend = b or backend
        skill_model = m

    judge_backend, judge_model_name = DEFAULT_BACKEND, None
    if judge_model:
        jb, jm = parse_target(judge_model)
        judge_backend = jb or judge_backend
        judge_model_name = jm

    name = spec_name(spec_file)
    print_banner(name, k, backend, skill_model)

    harness = get_harness(backend, skill_model)
    judge = EvalJudge(judge_backend, judge_model_name)

    task_names = [t.name for t in spec.tasks]
    progress, task_ids = make_progress(task_names, k)

    attempt_counts: dict[str, int] = {t.name: 0 for t in spec.tasks}
    pass_counts: dict[str, int] = {t.name: 0 for t in spec.tasks}
    unusable_counts: dict[str, int] = {t.name: 0 for t in spec.tasks}

    def on_attempt_done(event: AttemptEvent) -> None:
        task = next((t for t in spec.tasks if t.id == event.task_id), None)
        if task is None:
            return
        attempt_counts[task.name] += 1
        if event.outcome == Outcome.PASS:
            pass_counts[task.name] += 1
        if not event.outcome.is_usable:
            unusable_counts[task.name] += 1
            # Surface noise the moment it lands so a watching agent/human can stop.
            progress.console.print(
                f"[yellow]⊘[/yellow] {task.name} · attempt {event.attempt}: "
                f"[yellow]{event.outcome.value}[/yellow]"
            )
        update_progress(
            progress,
            task_ids,
            task.name,
            k,
            attempt_counts[task.name],
            pass_counts[task.name],
            cheated=event.outcome == Outcome.CHEAT,
            unusable=unusable_counts[task.name],
        )

    def on_task_done(result: TaskResult) -> None:
        if len(result.attempts) >= k:
            return
        update_progress(
            progress,
            task_ids,
            result.task_name,
            k,
            len(result.attempts),
            result.successes,
            cheated=any(
                attempt.outcome == Outcome.CHEAT for attempt in result.attempts
            ),
            unusable=result.unusable,
            finished=True,
        )

    with progress:
        try:
            results = run(
                spec=spec,
                spec_path=spec_file,
                harness=harness,
                judge=judge,
                backend=backend,
                model=skill_model,
                k=k,
                workers=workers,
                timeout=timeout,
                fail_fast_unusable=fail_fast_unusable,
                baseline=baseline,
                on_attempt_done=on_attempt_done,
                on_task_done=on_task_done,
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
