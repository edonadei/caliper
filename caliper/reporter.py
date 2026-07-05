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
    UsageTotals,
)

console = Console()


def _supports_unicode() -> bool:
    encoding = getattr(console.file, "encoding", None) or ""
    return "utf" in encoding.lower()


def _fmt_tokens(n: int) -> str:
    """Compact token count: 1_200_000 -> '1.2M', 340_000 -> '340K'."""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.0f}K"
    return str(n)


def _fmt_duration(seconds: float) -> str:
    """Wall-clock time: '42s', '6m 18s', '1h 2m'."""
    total = int(round(seconds))
    if total < 60:
        return f"{total}s"
    if total < 3600:
        return f"{total // 60}m {total % 60}s"
    return f"{total // 3600}h {(total % 3600) // 60}m"


_UNICODE = _supports_unicode()
_BANNER = "[bold cyan]CALIPER[/bold cyan]"
_SEP = "·" if _UNICODE else "-"
_RULE = "—" if _UNICODE else "-"
_WARN = "⚠" if _UNICODE else "!"
_CHECK = "✓" if _UNICODE else "OK"
_CROSS = "✗" if _UNICODE else "X"


def _engine_label(backend: str | None, model: str | None) -> str:
    """`backend · model` when a model is known, else just the backend."""
    label = backend or "?"
    return f"{label} {_SEP} {model}" if model else label


def _judge_suffix(backend: str | None, model: str | None) -> str:
    """A ` · judge <engine>` fragment, or empty when the judge is unrecorded."""
    if not backend:
        return ""
    return f"  {_SEP}  judge {_engine_label(backend, model)}"


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

    judge_suffix = _judge_suffix(results.run.judge_backend, results.run.judge_model)
    console.print()
    console.rule(
        f"{_BANNER}  {_RULE}  [bold]{spec}[/bold]  ([cyan]{backend}[/cyan]"
        + (f" {_SEP} [dim]{model}[/dim]" if model else "")
        + f"){judge_suffix}  {_RULE}  {ts}",
        style="cyan",
    )
    console.print()

    table = Table(
        box=box.ROUNDED, show_header=True, header_style="bold cyan", expand=False
    )
    table.add_column("Task")
    table.add_column(f"k ({k})", justify="center")
    table.add_column("pass@k", justify="right")
    table.add_column("Tokens", justify="right", style="dim")
    table.add_column("Wall", justify="right", style="dim")
    table.add_column("", justify="center")

    for tr in results.task_results:
        cheated_count = sum(1 for a in tr.attempts if a.cheated)
        status_text = _status_cell(tr, k, cheated_count > 0)
        pass_at_k = "—" if tr.pass_at_k is None else f"{tr.pass_at_k * 100:.1f}%"
        totals = UsageTotals.from_task_results([tr])
        tokens_cell = (
            _fmt_tokens(totals.total_tokens) if totals.tokens_reported else _RULE
        )
        wall_cell = _fmt_duration(totals.wall_seconds)
        table.add_row(
            tr.task_name,
            f"{tr.successes}/{k}",
            pass_at_k,
            tokens_cell,
            wall_cell,
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
    with_totals = UsageTotals.from_task_results(results.task_results)
    console.print()
    _print_usage_summary(with_totals, results.baseline_usage)
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


def _usage_delta_cell(skill_val: float, base_val: float) -> str:
    """A tidy `+305% vs no skill` cell. Green when the skill is *cheaper* (uses
    less), red when costlier — same convention as ``compare``, never a pass/fail
    signal. Percent only; the absolute totals are already in the value column."""
    delta = skill_val - base_val
    if delta == 0:
        return f"[dim]{_RULE} vs no skill[/dim]"
    color = "green" if delta < 0 else "red"
    sign = "+" if delta > 0 else "-"
    pct = (
        f"{abs(delta / base_val * 100):.0f}%"
        if base_val > 0
        else _fmt_tokens(int(abs(delta)))
    )
    return f"[{color}]{sign}{pct}[/{color}] [dim]vs no skill[/dim]"


def _print_usage_summary(totals: UsageTotals, baseline: UsageTotals | None) -> None:
    """The cost block: tokens + wall time as an aligned grid under the pass@k
    bars, with an optional `vs no skill` delta column. Cost/latency is a
    first-class axis (CONTEXT.md → Run usage totals); dollar cost is deliberately
    out of scope."""
    if totals.attempts == 0:
        return

    show_delta = baseline is not None and baseline.attempts > 0
    grid = Table.grid(padding=(0, 3))
    grid.add_column(style="bold")  # metric
    grid.add_column()  # value
    if show_delta:
        grid.add_column()  # vs no skill

    if totals.tokens_reported:
        tokens_val = (
            f"{_fmt_tokens(totals.prompt_tokens)} in / "
            f"{_fmt_tokens(totals.output_tokens)} out"
        )
    else:
        tokens_val = f"[dim]{_RULE}[/dim]"

    wall_val = _fmt_duration(totals.wall_seconds)
    if totals.usable_attempts > 0:
        avg = totals.usable_wall_seconds / totals.usable_attempts
        wall_val += f"  [dim]{avg:.1f}s per attempt[/dim]"

    if show_delta:
        tokens_delta = (
            _usage_delta_cell(totals.total_tokens, baseline.total_tokens)
            if totals.tokens_reported and baseline.tokens_reported
            else ""
        )
        grid.add_row(" Tokens", tokens_val, tokens_delta)
        grid.add_row(
            " Wall",
            wall_val,
            _usage_delta_cell(totals.wall_seconds, baseline.wall_seconds),
        )
    else:
        grid.add_row(" Tokens", tokens_val)
        grid.add_row(" Wall", wall_val)

    console.print(grid)

    if totals.unusable_attempts > 0:
        pieces = []
        if totals.tokens_reported:
            pieces.append(f"{_fmt_tokens(totals.unusable_tokens)} tokens")
        pieces.append(_fmt_duration(totals.unusable_wall_seconds))
        detail = ", ".join(pieces)
        plural = "s" if totals.unusable_attempts > 1 else ""
        console.print(
            f" [yellow]{_UNUSABLE} unusable spend:[/yellow] [dim]{detail}  "
            f"({totals.unusable_attempts} attempt{plural}, not counted in the "
            f"average)[/dim]"
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
        meta = f"{attempt.duration_seconds:.1f}s"
        if attempt.usage is not None and attempt.usage.total_tokens is not None:
            meta += f" {_SEP} {_fmt_tokens(attempt.usage.total_tokens)} tok"
        lines.append(f"  Attempt {attempt.attempt}  {prefix}{label}  ({meta})")
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
            title=f"[bold]{tr.task_name}[/bold]",
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
    a_engine = _engine_label(comp.a.backend, comp.a.model)
    b_engine = _engine_label(comp.b.backend, comp.b.model)
    console.print(
        f"    [bold]A[/bold] {a_ts} ([cyan]{a_engine}[/cyan])   {_SEP}"
        f"   [bold]B[/bold] {b_ts} ([cyan]{b_engine}[/cyan])   {_SEP}"
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

    _print_comparison_usage(comp)

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


def _usage_delta(label: str, a_val: float, b_val: float, fmt) -> str:
    """One `label  A x  B y  Δ ±p% (±abs)` row. Green when B is cheaper (a win),
    red when costlier — but this NEVER flips has_regression (CONTEXT.md →
    Regression). Dim when equal or A has no baseline to compute a percentage."""
    delta = b_val - a_val
    row = f" [bold]{label}[/bold]  A {fmt(a_val)}  B {fmt(b_val)}   {_delta_symbol()} "
    if delta == 0:
        return row + f"[dim]{_RULE}[/dim]"
    color = "green" if delta < 0 else "red"
    sign = "+" if delta > 0 else "-"
    abs_part = fmt(abs(delta))
    if a_val > 0:
        pct = delta / a_val * 100
        return row + f"[{color}]{sign}{abs(pct):.0f}% ({sign}{abs_part})[/{color}]"
    return row + f"[{color}]{sign}{abs_part}[/{color}]"


def _print_comparison_usage(comp: RunComparison) -> None:
    """Token + wall-clock delta rows under the pass@k headline. Secondary signals:
    a token/time drop is a win, not a regression — see CONTEXT.md → Regression."""
    a, b = comp.a_usage, comp.b_usage
    if a.tokens_reported and b.tokens_reported:
        console.print(
            _usage_delta(
                "Tokens", a.total_tokens, b.total_tokens, lambda n: _fmt_tokens(int(n))
            )
        )
    console.print(_usage_delta("Wall  ", a.wall_seconds, b.wall_seconds, _fmt_duration))


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
