from __future__ import annotations


from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.progress import (
    Progress,
    SpinnerColumn,
    TaskID,
    TextColumn,
    TimeElapsedColumn,
)
from rich.table import Table
from rich.table import Column
from rich.text import Text

from caliper.schema.results import (
    Outcome,
    RunComparison,
    RunResults,
    TaskComparison,
    TaskResult,
)

console = Console()


def _supports_unicode() -> bool:
    encoding = getattr(console.file, "encoding", None) or ""
    return "utf" in encoding.lower()


_UNICODE = _supports_unicode()
_BANNER = "[bold cyan]CALIPER[/bold cyan]"
_SEP = "·" if _UNICODE else "-"
_RULE = "—" if _UNICODE else "-"
_WARN = "⚠" if _UNICODE else "!"
_CHECK = "✓" if _UNICODE else "OK"
_CROSS = "✗" if _UNICODE else "X"
_UP = "↑" if _UNICODE else "up"
_DOWN = "↓" if _UNICODE else "down"
_BAR_FULL = "█" if _UNICODE else "#"
_BAR_EMPTY = "░" if _UNICODE else "-"
_UNUSABLE = "⊘" if _UNICODE else "o"

# Per-outcome glyph for the per-attempt detail view. Usable failures read as
# failures; the three noise outcomes get the distinct ⊘ marker.
_OUTCOME_GLYPH = {
    Outcome.PASS: f"[green]{_CHECK}[/green]",
    Outcome.TASK_FAIL: f"[red]{_CROSS}[/red]",
    Outcome.CHEAT: f"[yellow]{_WARN}[/yellow]",
    Outcome.INFRA_ERROR: f"[yellow]{_UNUSABLE}[/yellow]",
    Outcome.TIMEOUT: f"[yellow]{_UNUSABLE}[/yellow]",
    Outcome.JUDGE_ERROR: f"[yellow]{_UNUSABLE}[/yellow]",
}


def print_banner(
    spec_name: str, k: int, backend: str, model: str | None = None
) -> None:
    target = f"[cyan]{backend}[/cyan]" + (
        f" [dim]{_SEP} {model}[/dim]" if model else ""
    )
    console.print(
        Panel(
            f"{_BANNER}  {_SEP}  [bold]{spec_name}[/bold]  {_SEP}  k=[cyan]{k}[/cyan]  {_SEP}  {target}",
            border_style="cyan",
            padding=(0, 2),
        )
    )


def make_progress(tasks: list[str], k: int) -> tuple[Progress, dict[str, TaskID]]:
    progress = Progress(
        SpinnerColumn(),
        TextColumn(
            "[bold]{task.description}",
            justify="left",
            table_column=Column(width=40, overflow="ellipsis", no_wrap=True),
        ),
        TextColumn(
            "[cyan]{task.completed}/{task.total}",
            table_column=Column(width=5, no_wrap=True),
        ),
        TimeElapsedColumn(),
        TextColumn("{task.fields[status]}", table_column=Column(width=7, no_wrap=True)),
        console=console,
        expand=False,
        transient=True,
    )
    task_ids: dict[str, TaskID] = {}
    for name in tasks:
        tid = progress.add_task(name, total=k, status="")
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
    unusable: int = 0,
    finished: bool = False,
) -> None:
    tid = task_ids.get(task_name)
    if tid is None:
        return
    terminal = completed == k or finished
    if cheated:
        status = f"[bold yellow]{_WARN} cheat[/bold yellow]"
    elif terminal and unusable:
        status = f"[bold yellow]{_UNUSABLE}{unusable}[/bold yellow]"
    elif terminal:
        status = (
            f"[bold green]{_CHECK}[/bold green]"
            if passed == k
            else f"[bold red]{_CROSS}[/bold red]"
        )
    else:
        status = f"[dim]{completed}/{k}[/dim]"
    rendered_completed = k if finished and completed < k else completed
    progress.update(tid, total=k, completed=rendered_completed, status=status)


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

    table = Table(
        box=box.ROUNDED, show_header=True, header_style="bold cyan", expand=False
    )
    table.add_column("ID", style="dim", no_wrap=True)
    table.add_column("Task")
    table.add_column(f"k ({k})", justify="center")
    table.add_column("pass@k", justify="right")
    table.add_column("", justify="center")

    for tr in results.task_results:
        cheated_count = sum(1 for a in tr.attempts if a.cheated)
        status_text = _status_cell(tr, k, cheated_count > 0)
        pass_at_k = "—" if tr.pass_at_k is None else f"{tr.pass_at_k * 100:.1f}%"
        table.add_row(
            tr.task_id,
            tr.task_name,
            f"{tr.successes}/{k}",
            pass_at_k,
            status_text,
        )

    console.print(table)
    console.print()

    _print_aggregate(results)

    tasks_to_detail = (
        results.task_results
        if verbose
        else [
            tr
            for tr in results.task_results
            if tr.pass_at_k is None or tr.pass_at_k < 1.0
        ]
    )
    if tasks_to_detail:
        console.print()
        for tr in tasks_to_detail:
            _print_task_detail(tr, k)


def _status_cell(tr: TaskResult, k: int, any_cheat: bool) -> Text:
    if any_cheat:
        return Text(f"{_WARN} CHEAT", style="bold yellow")
    if len(tr.attempts) < k and tr.pass_at_k is None:
        return Text(f"{_UNUSABLE} ABORTED", style="bold yellow")
    if tr.pass_at_k is None:
        return Text(f"{_UNUSABLE} UNUSABLE", style="bold yellow")
    suffix = f" ({tr.unusable} {_UNUSABLE})" if tr.unusable else ""
    if tr.pass_at_k >= 0.99:
        return Text(f"{_CHECK} PASS{suffix}", style="bold green")
    elif tr.successes == 0:
        return Text(f"{_CROSS} FAIL{suffix}", style="bold red")
    else:
        return Text(f"~ PARTIAL{suffix}", style="bold yellow")


def _print_aggregate(results: RunResults) -> None:
    agg = results.aggregate

    def score_bar(score: float, width: int = 20) -> str:
        filled = round(score * width)
        return (
            "[green]"
            + _BAR_FULL * filled
            + "[/green][dim]"
            + _BAR_EMPTY * (width - filled)
            + "[/dim]"
        )

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

    _print_unusable_summary(results)
    console.print()


def _print_unusable_summary(results: RunResults) -> None:
    """One line, only when there is noise to report, so a clean run is unchanged."""
    counts: dict[Outcome, int] = {}
    for tr in results.task_results:
        for a in tr.attempts:
            if not a.outcome.is_usable:
                counts[a.outcome] = counts.get(a.outcome, 0) + 1
    total = sum(counts.values())
    if not total:
        return
    breakdown = " · ".join(
        f"{n} {o.value}" for o, n in sorted(counts.items(), key=lambda kv: kv[0].value)
    )
    console.print(
        f" [yellow]{_UNUSABLE} {total} unusable[/yellow]  [dim]({breakdown}) "
        f"— excluded from pass@k[/dim]"
    )


_OUTPUT_TRUNCATE_AT = 500


def _format_output(output: str) -> str:
    if not output or not output.strip():
        return "[dim][no output][/dim]"
    if len(output) > _OUTPUT_TRUNCATE_AT:
        tail = output[-_OUTPUT_TRUNCATE_AT:]
        return f"[dim][...truncated, showing last {_OUTPUT_TRUNCATE_AT} chars][/dim]\n{tail}"
    return output


def _print_task_detail(tr: TaskResult, k: int) -> None:
    lines: list[str] = []
    if len(tr.attempts) < k and tr.pass_at_k is None:
        lines.append(
            f"  [yellow]ABORTED[/yellow] after {len(tr.attempts)}/{k} attempts"
        )
    for attempt in tr.attempts:
        prefix = _OUTCOME_GLYPH.get(attempt.outcome, f"[red]{_CROSS}[/red]")
        label = (
            ""
            if attempt.outcome.is_usable
            else f"  [yellow]{attempt.outcome.value}[/yellow]"
        )
        lines.append(
            f"  Attempt {attempt.attempt}  {prefix}{label}  ({attempt.duration_seconds:.1f}s)"
        )
        if attempt.cheated:
            for ev in attempt.cheat_evidence:
                lines.append(f"    [yellow]cheat:[/yellow] {ev}")
        lines.append(f"    [dim]output:[/dim] {_format_output(attempt.output)}")
        if attempt.assert_evidence:
            lines.append(f"    [dim]assert: {attempt.assert_evidence}[/dim]")
        if attempt.autorater_reasoning:
            lines.append(f"    [dim]{attempt.autorater_reasoning}[/dim]")

    console.print(
        Panel(
            "\n".join(lines),
            title=f"[bold]{tr.task_id}[/bold] {tr.task_name}",
            border_style="dim",
        )
    )


# Per-outcome (glyph, style) for building the side-by-side attempt strips as
# rich Text, so colour survives regardless of markup mode.
_OUTCOME_STYLE = {
    Outcome.PASS: (_CHECK, "green"),
    Outcome.TASK_FAIL: (_CROSS, "red"),
    Outcome.CHEAT: (_WARN, "yellow"),
    Outcome.INFRA_ERROR: (_UNUSABLE, "yellow"),
    Outcome.TIMEOUT: (_UNUSABLE, "yellow"),
    Outcome.JUDGE_ERROR: (_UNUSABLE, "yellow"),
}


def _fmt_score(score: float | None) -> str:
    return _RULE if score is None else f"{score * 100:.1f}%"


def _outcome_strip(outcomes: list[Outcome]) -> Text:
    strip = Text()
    for oc in outcomes:
        glyph, style = _OUTCOME_STYLE.get(oc, (_CROSS, "red"))
        strip.append(glyph, style=style)
    return strip


def _delta_cell(tc: TaskComparison) -> Text:
    # Unmeasured (None) and no-change (0.0) both read as "—"; JSON keeps them
    # distinct. Only a real move shows a signed number.
    if tc.delta is None or tc.delta == 0:
        return Text(_RULE, style="dim")
    sign = "+" if tc.delta > 0 else ""
    style = "red" if tc.regression else "green"
    return Text(f"{sign}{tc.delta * 100:.1f}%", style=style)


def print_comparison(comp: RunComparison) -> None:
    """Render a two-run diff. A thin shell over ``diff_runs`` — no logic here."""
    a_ts = comp.a.timestamp.strftime("%Y-%m-%dT%H-%M-%SZ")
    b_ts = comp.b.timestamp.strftime("%Y-%m-%dT%H-%M-%SZ")

    console.print()
    console.rule(
        f"{_BANNER}  {_RULE}  compare  {_RULE}  [bold]{comp.a.spec}[/bold]",
        style="cyan",
    )
    console.print(
        f"    [bold]A[/bold] {a_ts} ([cyan]{comp.a.backend}[/cyan])   {_SEP}"
        f"   [bold]B[/bold] {b_ts} ([cyan]{comp.b.backend}[/cyan])   {_SEP}"
        f"   k=[cyan]{comp.a.k}[/cyan]"
        + (f"/[cyan]{comp.b.k}[/cyan]" if comp.k_mismatch else "")
    )
    for warning in comp.warnings:
        console.print(f" [bold yellow]{_WARN}[/bold yellow] [yellow]{warning}[/yellow]")
    console.print()

    table = Table(
        box=box.ROUNDED, show_header=True, header_style="bold cyan", expand=False
    )
    table.add_column("Task")
    table.add_column("A pass@k", justify="right")
    table.add_column("B pass@k", justify="right")
    table.add_column(f"{_delta_symbol()}", justify="right")
    table.add_column("A strip")
    table.add_column("B strip")

    for tc in comp.matched:
        unmeasured = tc.a_score is None or tc.b_score is None
        name = Text(tc.task_name, style="dim" if unmeasured else "")
        a_pk = Text(_fmt_score(tc.a_score), style="dim" if unmeasured else "")
        b_pk = Text(_fmt_score(tc.b_score), style="dim" if unmeasured else "")
        table.add_row(
            name,
            a_pk,
            b_pk,
            _delta_cell(tc),
            _outcome_strip(tc.a_outcomes),
            _outcome_strip(tc.b_outcomes),
        )

    console.print(table)
    console.print()
    _print_comparison_summary(comp)


def _delta_symbol() -> str:
    return "Δ" if _UNICODE else "delta"


def _print_comparison_summary(comp: RunComparison) -> None:
    arrow = _UP if comp.aggregate_delta >= 0 else _DOWN
    sign = "+" if comp.aggregate_delta >= 0 else ""
    color = "green" if comp.aggregate_delta >= 0 else "red"
    console.print(
        f" [bold]A[/bold] [cyan]{comp.a_matched_avg * 100:.1f}%[/cyan]   "
        f"[bold]B[/bold] [cyan]{comp.b_matched_avg * 100:.1f}%[/cyan]   "
        f"[bold]{_delta_symbol()} (matched)[/bold] "
        f"[{color}]{sign}{comp.aggregate_delta * 100:.1f}%[/{color}] {arrow}"
    )

    regressions = [tc.task_name for tc in comp.matched if tc.regression]
    if regressions:
        console.print(
            f" [bold yellow]{_WARN}[/bold yellow] [yellow]{len(regressions)} "
            f"regression{'s' if len(regressions) > 1 else ''}:[/yellow] "
            f"{', '.join(regressions)}"
        )

    unmeasured = [
        tc.task_name for tc in comp.matched if tc.a_score is None or tc.b_score is None
    ]
    if unmeasured:
        console.print(
            f" [yellow]{_UNUSABLE}[/yellow] [dim]{len(unmeasured)} unmeasured "
            f"(excluded from {_delta_symbol()}): {', '.join(unmeasured)}[/dim]"
        )

    if comp.unmatched_a or comp.unmatched_b:
        only_a = ", ".join(comp.unmatched_a) or "—"
        only_b = ", ".join(comp.unmatched_b) or "—"
        console.print(
            f" [dim]unmatched — only in A: {only_a}   only in B: {only_b}[/dim]"
        )
    console.print()


def results_to_json(results: RunResults) -> str:
    return results.model_dump_json(indent=2)


def comparison_to_json(comp: RunComparison) -> str:
    return comp.model_dump_json(indent=2)


def save_results(results: RunResults, spec_path: str) -> str:
    from pathlib import Path

    spec_p = Path(spec_path)
    out_dir = spec_p.parent / ".caliper" / "results" / results.run.spec
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = results.run.timestamp.strftime("%Y-%m-%dT%H-%M-%SZ")
    out_file = out_dir / f"{ts}.json"
    out_file.write_text(results_to_json(results))
    return str(out_file)
