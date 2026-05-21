from __future__ import annotations


from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskID,
    TextColumn,
    TimeElapsedColumn,
)
from rich.table import Table
from rich.text import Text

from verdict.schema.results import RunResults, TaskResult

console = Console()


def _supports_unicode() -> bool:
    encoding = getattr(console.file, "encoding", None) or ""
    return "utf" in encoding.lower()


_UNICODE = _supports_unicode()
_BANNER = "[bold cyan]VERDICT[/bold cyan]"
_SEP = "·" if _UNICODE else "-"
_RULE = "—" if _UNICODE else "-"
_WARN = "⚠" if _UNICODE else "!"
_CHECK = "✓" if _UNICODE else "OK"
_CROSS = "✗" if _UNICODE else "X"
_UP = "↑" if _UNICODE else "up"
_DOWN = "↓" if _UNICODE else "down"
_BAR_FULL = "█" if _UNICODE else "#"
_BAR_EMPTY = "░" if _UNICODE else "-"


def print_banner(spec_name: str, k: int, backend: str) -> None:
    console.print(
        Panel(
            f"{_BANNER}  {_SEP}  [bold]{spec_name}[/bold]  {_SEP}  k=[cyan]{k}[/cyan]  {_SEP}  [cyan]{backend}[/cyan]",
            border_style="cyan",
            padding=(0, 2),
        )
    )


def make_progress(tasks: list[str]) -> tuple[Progress, dict[str, TaskID]]:
    progress = Progress(
        SpinnerColumn(),
        TextColumn("[bold]{task.description}", justify="right"),
        BarColumn(bar_width=20),
        TextColumn("[cyan]{task.completed}/{task.total}"),
        TimeElapsedColumn(),
        TextColumn("{task.fields[status]}"),
        console=console,
        expand=False,
    )
    task_ids: dict[str, TaskID] = {}
    for name in tasks:
        tid = progress.add_task(name[:30], total=None, status="")
        task_ids[name] = tid
    return progress, task_ids


def update_progress(
    progress: Progress,
    task_ids: dict[str, TaskID],
    task_name: str,
    k: int,
    completed: int,
    passed: int,
    cheated: bool = False,
) -> None:
    tid = task_ids.get(task_name)
    if tid is None:
        return
    if cheated:
        status = f"[bold yellow]{_WARN} cheat[/bold yellow]"
    elif completed == k:
        status = f"[bold green]{_CHECK}[/bold green]" if passed == k else f"[bold red]{_CROSS}[/bold red]"
    else:
        status = f"[dim]{completed}/{k}[/dim]"
    progress.update(tid, total=k, completed=completed, status=status)


def print_results(results: RunResults, verbose: bool = False) -> None:
    spec = results.run.spec
    backend = results.run.backend
    model = results.run.model or ""
    ts = results.run.timestamp.strftime("%Y-%m-%d %H:%M")
    k = results.run.k

    console.print()
    console.rule(
        f"{_BANNER}  {_RULE}  [bold]{spec}[/bold]  ([cyan]{backend}[/cyan]"
        + (f" {_SEP} [dim]{model}[/dim]" if model else "")
        + f")  {_RULE}  {ts}",
        style="cyan",
    )
    console.print()

    table = Table(box=box.ROUNDED, show_header=True, header_style="bold cyan", expand=False)
    table.add_column("ID", style="dim", no_wrap=True)
    table.add_column("Task")
    table.add_column(f"k ({k})", justify="center")
    table.add_column("pass@k", justify="right")
    table.add_column("", justify="center")

    for tr in results.task_results:
        cheated_count = sum(1 for a in tr.attempts if a.cheated)
        status_text = _status_cell(tr, cheated_count > 0)
        table.add_row(
            tr.task_id,
            tr.task_name,
            f"{tr.successes}/{k}",
            f"{tr.pass_at_k * 100:.1f}%",
            status_text,
        )

    console.print(table)
    console.print()

    _print_aggregate(results)

    if verbose:
        console.print()
        for tr in results.task_results:
            _print_task_detail(tr, k)


def _status_cell(tr: TaskResult, any_cheat: bool) -> Text:
    if any_cheat:
        return Text(f"{_WARN} CHEAT", style="bold yellow")
    if tr.successes == tr.pass_at_k * len(tr.attempts) and tr.successes == len(tr.attempts):
        pass
    if tr.pass_at_k >= 0.99:
        return Text(f"{_CHECK} PASS", style="bold green")
    elif tr.successes == 0:
        return Text(f"{_CROSS} FAIL", style="bold red")
    else:
        return Text("~ PARTIAL", style="bold yellow")


def _print_aggregate(results: RunResults) -> None:
    agg = results.aggregate

    def score_bar(score: float, width: int = 20) -> str:
        filled = round(score * width)
        return "[green]" + _BAR_FULL * filled + "[/green][dim]" + _BAR_EMPTY * (width - filled) + "[/dim]"

    console.print(
        f" [bold]With skill[/bold]    [cyan]{agg.avg_pass_at_k * 100:.1f}%[/cyan]  {score_bar(agg.avg_pass_at_k)}"
    )

    if results.baseline:
        base = results.baseline
        console.print(
            f" [dim]No skill[/dim]      [dim]{base.avg_pass_at_k * 100:.1f}%[/dim]  {score_bar(base.avg_pass_at_k)}"
        )

    if results.delta:
        delta = results.delta.delta
        sign = "+" if delta >= 0 else ""
        color = "green" if delta >= 0 else "red"
        arrow = _UP if delta >= 0 else _DOWN
        console.print(
            f" [bold]Delta[/bold]        [{color}]{sign}{delta * 100:.1f}%[/{color}]  {arrow}"
        )

    console.print()


def _print_task_detail(tr: TaskResult, k: int) -> None:
    lines: list[str] = []
    for attempt in tr.attempts:
        prefix = (
            f"[green]{_CHECK}[/green]"
            if attempt.passed
            else (f"[yellow]{_WARN}[/yellow]" if attempt.cheated else f"[red]{_CROSS}[/red]")
        )
        lines.append(f"  Attempt {attempt.attempt}  {prefix}  ({attempt.duration_seconds:.1f}s)")
        if attempt.cheated:
            for ev in attempt.cheat_evidence:
                lines.append(f"    [yellow]cheat:[/yellow] {ev}")
        if attempt.autorater_reasoning:
            lines.append(f"    [dim]{attempt.autorater_reasoning}[/dim]")
        if attempt.assert_evidence:
            lines.append(f"    [dim]assert: {attempt.assert_evidence}[/dim]")

    console.print(
        Panel(
            "\n".join(lines),
            title=f"[bold]{tr.task_id}[/bold] {tr.task_name}",
            border_style="dim",
        )
    )


def results_to_json(results: RunResults) -> str:
    return results.model_dump_json(indent=2)


def save_results(results: RunResults, spec_path: str) -> str:
    from pathlib import Path

    spec_p = Path(spec_path)
    out_dir = spec_p.parent / ".verdict" / "results" / results.run.spec
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = results.run.timestamp.strftime("%Y-%m-%dT%H-%M-%SZ")
    out_file = out_dir / f"{ts}.json"
    out_file.write_text(results_to_json(results))
    return str(out_file)
