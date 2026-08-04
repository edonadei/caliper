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
_TO = "→" if _UNICODE else "->"
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
    # Dim, not yellow: nothing was asked, so nothing went wrong.
    Outcome.NOT_CHECKED: f"[dim]{_RULE}[/dim]",
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
    # A --baseline run is a two-run diff (no skill vs with skill), so render it
    # through the exact same comparison view as `caliper compare`.
    if results.baseline_task_results is not None:
        from caliper.compare import diff_baseline

        print_comparison(diff_baseline(results), verbose=verbose)
        # compare's table has no activation half — the no-skill arm installs
        # nothing, so there is nothing to diff — but the with-skill arm's
        # activation numbers are still real and must not disappear.
        _print_activation_aggregate(results)
        console.print()
        # The compare table shows *which* attempts failed (the strips); the panels
        # below show *why* (output, assert evidence, autorater reasoning) for the
        # with-skill run — the strips alone don't, and there's no separate run to
        # `caliper report` for a --baseline diff.
        _print_task_details(results.task_results, results.run.k, verbose)
        return

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

    _print_score(results)
    console.print()

    table = Table(
        box=box.ROUNDED, show_header=True, header_style="bold cyan", expand=False
    )
    table.add_column("Task")
    table.add_column(f"k ({k})", justify="center")
    table.add_column("success", justify="right")
    if verbose:
        table.add_column("pass@k", justify="right", style="dim")
        table.add_column("pass^k", justify="right", style="dim")
    # A verdict, not a list of names: the counts live on the per-skill table.
    # Dim "—" when the task asserted nothing.
    table.add_column("act", justify="center")
    table.add_column("Tokens", justify="right", style="dim")
    table.add_column("Wall", justify="right", style="dim")
    table.add_column("", justify="center")

    for tr in results.task_results:
        cheated_count = sum(1 for a in tr.attempts if a.cheated)
        status_text = _status_cell(tr, k, cheated_count > 0)
        totals = UsageTotals.from_task_results([tr])
        tokens_cell = (
            _fmt_tokens(totals.total_tokens) if totals.tokens_reported else _RULE
        )
        wall_cell = _fmt_duration(totals.wall_seconds)
        # A trigger-only task has no execution numbers to show; "0/3" would read
        # as three failures rather than three questions never asked.
        k_cell = _RULE if _is_trigger_only(tr) else f"{tr.successes}/{k}"
        row = [tr.task_name, k_cell, _fmt_score(tr.score)]
        if verbose:
            row += [_fmt_score(tr.pass_at_k), _fmt_score(tr.pass_hat_k)]
        row += [_activation_cell(tr), tokens_cell, wall_cell, status_text]
        table.add_row(*row)

    console.print(table)
    console.print()

    _print_activation_aggregate(results)
    _print_unusable_summary(results)
    console.print()
    _print_usage_summary(UsageTotals.from_task_results(results.task_results))
    console.print()
    _print_task_details(results.task_results, k, verbose)


def _print_task_details(task_results: list[TaskResult], k: int, verbose: bool) -> None:
    """Per-task failure panels: all tasks under ``--verbose``, else only the ones
    that did not fully pass. Shared by the single-run and --baseline reports."""
    tasks_to_detail = (
        task_results if verbose else [tr for tr in task_results if _needs_detail(tr)]
    )
    if tasks_to_detail:
        console.print()
        for tr in tasks_to_detail:
            _print_task_detail(tr, k)


def _needs_detail(tr: TaskResult) -> bool:
    """Whether a task earns a failure panel: either scoreboard came up short.

    A trigger-only task is judged solely on activation — its ``score`` is
    ``None`` by construction, and treating that as "didn't fully pass" would
    print a panel for every correct trigger probe.
    """
    activation_short = tr.activation_score is not None and tr.activation_score < 1.0
    if _is_trigger_only(tr):
        return activation_short
    return tr.score is None or tr.score < 1.0 or activation_short


def _is_trigger_only(tr: TaskResult) -> bool:
    """True when the task authored no execution check (`activates:` alone).

    Keyed on the *absence of any execution verdict*, not on unanimity: a single
    timeout among k would otherwise flip a correct trigger probe back to
    "0/3 UNUSABLE" — the exact reading `not_checked` exists to prevent.
    """
    if not any(a.outcome == Outcome.NOT_CHECKED for a in tr.attempts):
        return False
    return not any(a.outcome in (Outcome.PASS, Outcome.TASK_FAIL) for a in tr.attempts)


def _activation_cell(tr: TaskResult) -> Text:
    """Did this task's activation claim hold? A three-state verdict, 3 chars wide.

    Deliberately not the skill names: the *counts* live on the per-skill table,
    which is the axis they belong to. Spelling them out here forced a skill name
    into a per-task row and wrapped the whole table, and it degraded with every
    extra skill. What a reader needs while scanning for failures is narrower —
    a ``✗`` beside a 0% row says the description is the suspect, not the body.

    Counted over **activation-usable** attempts only, so a task whose every
    attempt timed out renders "—" rather than a confident verdict manufactured
    from an infrastructure failure.
    """
    score = tr.activation_score
    if score is None:
        return Text(_RULE, style="dim")
    if score >= 0.99:
        return Text(_CHECK, style="green")
    return Text(_CROSS, style="red")


def _status_cell(tr: TaskResult, k: int, any_cheat: bool) -> Text:
    if any_cheat:
        return Text(f"{_WARN} CHEAT", style="bold yellow")
    # An activates:-only task asked no execution question. Its silence is the
    # correct answer, so it reads as a dim skip — never a yellow error.
    if _is_trigger_only(tr):
        return Text(f"{_RULE} trigger only", style="dim")
    if len(tr.attempts) < k and tr.score is None:
        return Text(f"{_UNUSABLE} ABORTED", style="bold yellow")
    if tr.score is None:
        return Text(f"{_UNUSABLE} UNUSABLE", style="bold yellow")
    suffix = f" ({tr.unusable} {_UNUSABLE})" if tr.unusable else ""
    if tr.score >= 0.99:
        return Text(f"{_CHECK} PASS{suffix}", style="bold green")
    elif tr.successes == 0:
        return Text(f"{_CROSS} FAIL{suffix}", style="bold red")
    else:
        return Text(f"~ PARTIAL{suffix}", style="bold yellow")


def _score_bar(score: float, width: int = 20) -> str:
    filled = round(score * width)
    return (
        "[green]"
        + _BAR_FULL * filled
        + "[/green][dim]"
        + _BAR_EMPTY * (width - filled)
        + "[/dim]"
    )


def _print_score(results: RunResults) -> None:
    """The execution headline, printed *above* the per-task table it sums up."""
    agg = results.aggregate

    if agg.scored_tasks:
        plural = "s" if agg.scored_tasks != 1 else ""
        console.print(
            f" [bold]Score[/bold]       [cyan]{agg.avg_score * 100:.1f}%[/cyan]"
            f"  {_score_bar(agg.avg_score)}"
            f"  [dim]({agg.scored_tasks} task{plural} scored)[/dim]"
        )
    else:
        # Nothing was measured — an all-trigger-probe spec. "0.0%" with an empty
        # bar would read as total failure of a run where nothing failed.
        console.print(
            f" [bold]Score[/bold]       [dim]{_RULE}  no execution checks[/dim]"
        )


def _print_activation_aggregate(results: RunResults) -> None:
    """The activation half: one headline line, then a table on the *skill* axis.

    Split out so a ``--baseline`` report can print it too: that path renders
    through ``compare``, whose table has no activation half (the no-skill arm
    installs nothing, so there is nothing to diff) — and without this the
    activation numbers would silently vanish for anyone who passes
    ``--baseline``.

    Rendered only when the spec asserted ``activates:`` somewhere. A spec that
    never makes an activation claim should not grow a table of empty rows.
    """
    agg = results.aggregate
    if agg.avg_activation_score is None:
        return

    asserted = agg.activation_asserted or agg.activation_tasks
    plural = "s" if asserted != 1 else ""
    scope = (
        f"{asserted} asserted task{plural}"
        if agg.activation_tasks == asserted
        else f"{agg.activation_tasks} of {asserted} asserted task{plural} measured"
    )
    console.print(
        f" [bold]Activation[/bold]  "
        f"[cyan]{agg.avg_activation_score * 100:.1f}%[/cyan]"
        f"  {_score_bar(agg.avg_activation_score)}"
        f"  [dim]({scope})[/dim]"
    )
    if not agg.activation_per_skill:
        return

    # A skill never wanted and never seen had no chance to succeed or fail, so
    # it carries no measurement — only the fact that it was installed. At two
    # skills a row is fine; at ten it is eight identical rows burying the two
    # that matter, so those collapse to a single line below the table.
    measured = [s for s in agg.activation_per_skill if s.expected or s.fired]
    dormant = [s for s in agg.activation_per_skill if not (s.expected or s.fired)]

    if measured:
        console.print()
        table = Table(
            box=box.ROUNDED, show_header=True, header_style="bold cyan", expand=False
        )
        table.add_column("Skill")
        table.add_column("wanted", justify="right")
        # The same verb on both sides, so the pair reads as one behaviour
        # measured over two populations. The second is good-when-*low*, which
        # the cell colouring carries: a high hijack rate renders red.
        table.add_column("fires when wanted", justify="right")
        table.add_column("fires when not wanted", justify="right")
        # Worst first: the skill needing attention is the top row, not wherever
        # it happened to sit in the spec (peers have no meaningful order anyway).
        for stats in sorted(measured, key=_activation_severity):
            table.add_row(
                stats.skill,
                Text(f"{stats.expected} of {stats.total}", style="dim"),
                _rate_cell(stats.hits, stats.expected, stats.recall),
                _rate_cell(
                    stats.unwanted,
                    stats.opportunities,
                    stats.unwanted_rate,
                    higher_is_better=False,
                ),
            )
        console.print(table)

    if dormant:
        names = ", ".join(s.skill for s in dormant)
        plural = "s were" if len(dormant) > 1 else " was"
        console.print(
            f" [dim]{len(dormant)} more declared skill{plural} never wanted and "
            f"never fired: {names}[/dim]"
        )


def _activation_severity(stats) -> float:
    """Sort key: a skill's worst failure rate, worst first (descending).

    Both directions are converted to "how wrong is this" so they compare on one
    scale. A missing rate means the case never arose, which is not a failure, so
    it contributes nothing rather than counting as total failure.
    """
    missed = 1.0 - stats.recall if stats.recall is not None else 0.0
    over = stats.unwanted_rate if stats.unwanted_rate is not None else 0.0
    return -max(missed, over)


def _rate_cell(
    numerator: int,
    denominator: int,
    rate: float | None,
    *,
    higher_is_better: bool = True,
) -> Text:
    """``n/m   xx.x%``, dim "—" when the case never arose.

    A skill nothing ever expected has no fire-when-wanted rate, and one that
    never faced a prompt it should skip has no chance to over-fire. Neither is a
    zero. ``higher_is_better`` flips the colouring for the over-firing column,
    where the good value is 0%.
    """
    if rate is None or denominator <= 0:
        return Text(_RULE, style="dim")
    cell = Text(f"{numerator}/{denominator}".rjust(6))
    good = rate >= 0.99 if higher_is_better else rate <= 0.01
    cell.append(f"  {rate * 100:5.1f}%", style="green" if good else "red")
    return cell


def _print_unusable_summary(results: RunResults) -> None:
    """One line, only when there is noise to report, so a clean run is unchanged."""
    counts: dict[Outcome, int] = {}
    for tr in results.task_results:
        for a in tr.attempts:
            # `is_execution_noise`, not `not is_usable`: NOT_CHECKED is excluded
            # from the score without being an error, so a correct
            # activates:-only spec reports no noise at all.
            if a.outcome.is_execution_noise:
                counts[a.outcome] = counts.get(a.outcome, 0) + 1
    total = sum(counts.values())
    if not total:
        return
    breakdown = " · ".join(
        f"{n} {o.value}" for o, n in sorted(counts.items(), key=lambda kv: kv[0].value)
    )
    console.print(
        f" [yellow]{_UNUSABLE} {total} unusable[/yellow]  [dim]({breakdown}) "
        f"— excluded from the score[/dim]"
    )


def _print_usage_summary(totals: UsageTotals) -> None:
    """The cost block for a single run: tokens + wall time as an aligned grid.
    (A --baseline run renders the skill-vs-no-skill token/wall delta through the
    `compare` view instead.) Cost/latency is a first-class axis (docs/CONTEXT.md → Run
    usage totals); dollar cost is deliberately out of scope."""
    if totals.attempts == 0:
        return

    grid = Table.grid(padding=(0, 3))
    grid.add_column(style="bold")  # metric
    grid.add_column()  # value

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
    if len(tr.attempts) < k and tr.score is None and not _is_trigger_only(tr):
        lines.append(
            f"  [yellow]ABORTED[/yellow] after {len(tr.attempts)}/{k} attempts"
        )
    # A red activation row is unreadable without the claim it broke, so say what
    # the task expected before listing what each attempt actually reached for.
    if tr.activation_expected is not None:
        expected = ", ".join(tr.activation_expected) or "(nothing — silence)"
        lines.append(f"  [dim]expected to activate:[/dim] {expected}")
    for attempt in tr.attempts:
        prefix = _OUTCOME_GLYPH.get(attempt.outcome, f"[red]{_CROSS}[/red]")
        label = (
            ""
            if attempt.outcome.is_usable or attempt.outcome == Outcome.NOT_CHECKED
            else f"  [yellow]{attempt.outcome.value}[/yellow]"
        )
        meta = f"{attempt.duration_seconds:.1f}s"
        if attempt.usage is not None and attempt.usage.total_tokens is not None:
            meta += f" {_SEP} {_fmt_tokens(attempt.usage.total_tokens)} tok"
        lines.append(f"  Attempt {attempt.attempt}  {prefix}{label}  ({meta})")
        if attempt.cheated:
            for ev in attempt.cheat_evidence:
                lines.append(f"    [yellow]cheat:[/yellow] {ev}")
        if attempt.activation_passed is False:
            reached = ", ".join(attempt.activated or []) or "(nothing)"
            lines.append(f"    [red]activated:[/red] {reached}")
        elif attempt.activation_observed and attempt.activation_passed is None:
            # Nothing was asserted, so this is informational only — but it is
            # the one place the observation still surfaces now that the task
            # column carries a verdict rather than the skill names.
            reached = ", ".join(attempt.activated or []) or "(nothing)"
            lines.append(f"    [dim]activated: {reached}[/dim]")
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
    Outcome.NOT_CHECKED: (_RULE, "dim"),
}


def _fmt_score(score: float | None) -> str:
    return _RULE if score is None else f"{score * 100:.1f}%"


# Width of the widest score string ("100.0%"). The two operands of a `before →
# after` cell are padded to this so the arrows form a clean vertical column and
# the before/after values align across rows — the before-value left-aligned, the
# after-value right-aligned (a missing "—" then sits at the right, under the
# after column). A rate never exceeds 1.0, so 6 always fits.
_SCORE_W = len("100.0%")


def _score_pair(left: str, right: str, left_style: str, right_style: str) -> Text:
    """`left → right` with each side padded into a fixed sub-column so a stack of
    these cells aligns on the arrow and on both value columns."""
    cell = Text()
    cell.append(left.ljust(_SCORE_W), style=left_style)
    cell.append(f" {_TO} ", style="dim")
    cell.append(right.rjust(_SCORE_W), style=right_style)
    return cell


def _outcome_strip(outcomes: list[Outcome]) -> Text:
    # A space between glyphs so a symbol drawn wider than its one-cell slot
    # (notably ⊘) can't collide with its neighbour, and the strip stays legible.
    strip = Text()
    for i, oc in enumerate(outcomes):
        if i:
            strip.append(" ")
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


def _activation_score_cell(tc: TaskComparison) -> Text:
    """`before → after` for the activation scoreboard, styled on its own flag."""
    left = _fmt_score(tc.a_activation)
    right = _fmt_score(tc.b_activation)
    right_style = "red" if tc.activation_regression else ""
    return _score_pair(left, right, "dim", right_style)


def _activation_delta_cell(tc: TaskComparison) -> Text:
    if tc.activation_delta is None or tc.activation_delta == 0:
        return Text(_RULE, style="dim")
    sign = "+" if tc.activation_delta > 0 else ""
    style = "red" if tc.activation_regression else "green"
    return Text(f"{sign}{tc.activation_delta * 100:.1f}%", style=style)


def print_comparison(comp: RunComparison, verbose: bool = False) -> None:
    """Render a two-run diff. A thin shell over ``diff_runs`` — no logic here."""
    a_ts = comp.a.timestamp.strftime("%Y-%m-%dT%H-%M-%SZ")
    b_ts = comp.b.timestamp.strftime("%Y-%m-%dT%H-%M-%SZ")

    console.print()
    console.rule(
        f"{_BANNER}  {_RULE}  compare  {_RULE}  [bold]{comp.a.spec}[/bold]",
        style="cyan",
    )
    # Each side is titled by its explicit label (a --baseline diff sets "no skill"
    # / "with skill") or, for a plain compare, its timestamp + engine.
    a_desc = (
        comp.a_label
        or f"{a_ts} ([cyan]{_engine_label(comp.a.backend, comp.a.model)}[/cyan])"
    )
    b_desc = (
        comp.b_label
        or f"{b_ts} ([cyan]{_engine_label(comp.b.backend, comp.b.model)}[/cyan])"
    )
    # The two sides read as a transition (`before → after`), named once here, so
    # the table needs no A/B legend lookup.
    console.print(
        f"    {a_desc} {_TO} {b_desc}   {_SEP}"
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
    # Cells are fixed-width `before → after` pairs (see _score_pair), so they
    # already align internally; centering just sits the label over the block.
    table.add_column("success", justify="center")
    table.add_column(f"{_delta_symbol()}", justify="center")
    if verbose:
        table.add_column("pass@k", justify="center", style="dim")
        table.add_column("pass^k", justify="center", style="dim")
    # Only when at least one matched task asserted activation on both sides —
    # otherwise every cell would be "—" and the table just gets wider.
    show_activation = any(tc.activation_delta is not None for tc in comp.matched)
    if show_activation:
        table.add_column("activation", justify="center")
        table.add_column(f"{_delta_symbol()}", justify="center")
    table.add_column("attempts", justify="center")

    for tc in comp.matched:
        # A trigger-only task has no execution score by construction; dimming on
        # that alone would grey out a row whose activation diff is the point.
        unmeasured = (tc.a_score is None or tc.b_score is None) and (
            tc.activation_delta is None
        )
        name = Text(tc.task_name, style="dim" if unmeasured else "")
        row = [name, _score_cell(tc), _delta_cell(tc)]
        if verbose:
            row += [
                _alt_metric_cell(tc, "pass_at_k"),
                _alt_metric_cell(tc, "pass_hat_k"),
            ]
        if show_activation:
            row += [_activation_score_cell(tc), _activation_delta_cell(tc)]
        row.append(_attempts_cell(tc))
        table.add_row(*row)

    console.print(table)
    console.print()
    _print_comparison_summary(comp)


def _score_cell(tc: TaskComparison) -> Text:
    """`a% → b%` raw success rate; the 'after' is green on improvement, red on
    regression."""
    unmeasured = tc.a_score is None or tc.b_score is None
    after = "dim"
    if not unmeasured:
        after = "green" if tc.b_score > tc.a_score else "red" if tc.regression else ""
    return _score_pair(
        _fmt_score(tc.a_score),
        _fmt_score(tc.b_score),
        "dim" if unmeasured else "",
        after,
    )


def _alt_metric_cell(tc: TaskComparison, metric: str) -> Text:
    """`x% → y%` for a secondary metric (pass@k / pass^k), derived from the stored
    per-attempt outcomes so no extra fields are needed on the model."""
    from caliper.scoring import score_outcomes

    def val(outcomes: list[Outcome]) -> float | None:
        # score_outcomes owns the usable-denominator rule; render its result.
        return getattr(score_outcomes(outcomes), metric)

    return _score_pair(
        _fmt_score(val(tc.a_outcomes)), _fmt_score(val(tc.b_outcomes)), "", ""
    )


def _attempts_cell(tc: TaskComparison) -> Text:
    """`✓✗✗ → ✓✓✓`: the before-strip, an arrow, then the after-strip."""
    cell = _outcome_strip(tc.a_outcomes)
    cell.append(f" {_TO} ", style="dim")
    cell.append_text(_outcome_strip(tc.b_outcomes))
    return cell


def _delta_symbol() -> str:
    return "Δ" if _UNICODE else "delta"


def _usage_cells(a_val: float, b_val: float, fmt) -> tuple[str, str, str]:
    """The (before, after, `Δ …`) markup for one usage row. Green when the 'after'
    is cheaper (a win), red when costlier — but this NEVER flips has_regression
    (docs/CONTEXT.md → Regression). Dim when equal or there is no baseline to
    compute a percentage."""
    delta = b_val - a_val
    before, after = fmt(a_val), fmt(b_val)
    if delta == 0:
        note = f"[dim]{_RULE}[/dim]"
    else:
        color = "green" if delta < 0 else "red"
        sign = "+" if delta > 0 else "-"
        abs_part = fmt(abs(delta))
        if a_val > 0:
            note = f"[{color}]{sign}{abs(delta / a_val * 100):.0f}% ({sign}{abs_part})[/{color}]"
        else:
            note = f"[{color}]{sign}{abs_part}[/{color}]"
    return before, after, f"{_delta_symbol()} {note}"


def _print_comparison_summary(comp: RunComparison) -> None:
    delta = comp.aggregate_delta
    arrow = _UP if delta >= 0 else _DOWN
    sign = "+" if delta >= 0 else ""
    color = "green" if delta >= 0 else "red"
    after = "green" if delta > 0 else "red" if delta < 0 else "cyan"

    # One aligned grid so the metric labels, the `before → after` transition, and
    # the Δ notes each line up in a column instead of drifting with value length.
    _to = f"[dim]{_TO}[/dim]"
    grid = Table.grid(padding=(0, 1))
    grid.add_column(style="bold")  # metric label (leading space = indent)
    grid.add_column(justify="right")  # before
    grid.add_column()  # arrow
    grid.add_column(justify="left")  # after
    grid.add_column()  # Δ note
    grid.add_row(
        " Overall",
        f"[cyan]{comp.a_matched_avg * 100:.1f}%[/cyan]",
        _to,
        f"[{after}]{comp.b_matched_avg * 100:.1f}%[/{after}]",
        f"[bold]{_delta_symbol()} (matched)[/bold] "
        f"[{color}]{sign}{delta * 100:.1f}%[/{color}] {arrow}",
    )
    a, b = comp.a_usage, comp.b_usage
    if a.tokens_reported and b.tokens_reported:
        tb, ta, td = _usage_cells(
            a.total_tokens, b.total_tokens, lambda n: _fmt_tokens(int(n))
        )
        grid.add_row(" Tokens", tb, _to, ta, td)
    wb, wa, wd = _usage_cells(a.wall_seconds, b.wall_seconds, _fmt_duration)
    grid.add_row(" Wall", wb, _to, wa, wd)
    console.print(grid)

    # Reported on its own line, never merged into the execution regressions: a
    # description that stopped firing and a body that stopped working are fixed
    # in different places, so naming them together would hide which one moved.
    activation_regressions = [
        tc.task_name for tc in comp.matched if tc.activation_regression
    ]
    if activation_regressions:
        n = len(activation_regressions)
        console.print(
            f" [bold yellow]{_WARN}[/bold yellow] [yellow]{n} activation "
            f"regression{'s' if n > 1 else ''}:[/yellow] "
            f"{', '.join(activation_regressions)} "
            "[dim](the description stopped firing — not the body)[/dim]"
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
        a_name = comp.a_label or "A"
        b_name = comp.b_label or "B"
        only_a = ", ".join(comp.unmatched_a) or "—"
        only_b = ", ".join(comp.unmatched_b) or "—"
        console.print(
            f" [dim]unmatched — only in {a_name}: {only_a}   "
            f"only in {b_name}: {only_b}[/dim]"
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
