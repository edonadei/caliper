"""Terminal rendering for `caliper run`, `caliper report` and `caliper compare`.

Both commands share one visual system, so a reader who learns one view can read
the other:

* **Header.** A CALIPER badge, the command, the spec and k, then an aligned
  key/value block describing the environment (engine, judge, skills, MCP
  servers, setup, status). In ``compare`` the block has an A and a B column,
  and a row whose two sides are equal collapses to "same", so a difference is
  the thing that stands out.
* **One table per axis.** Tasks (execution), skills (activation), MCP servers
  (tool use). The two scoreboards are never blended (docs/adr/0014). Totals are
  each table's footer, never loose lines below it.
* **One rate cell.** The rate right-aligned in a fixed slot, then its evidence:
  the per-attempt marks at small k, a tally (``✓9 ✗1``) above that, so a row
  never outgrows its column. The live view fills the same marks in.
* **Deltas say what they are.** A rate delta is in percentage points (pp); a
  cost delta is relative, and never a regression (docs/CONTEXT.md →
  Regression).
* **Colour carries meaning only.** Green good, red bad, yellow "read this
  before trusting the number", cyan the thing under test, dim the rest.
* **Notes close every view** in one shape — glyph, bold label, detail, dim
  aside — sorted red, then yellow, then dim.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from rich import box
from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.progress import (
    Progress,
    ProgressColumn,
    SpinnerColumn,
    Task,
    TaskID,
    TextColumn,
    TimeElapsedColumn,
)
from rich.table import Column, Table
from rich.text import Text

from caliper.backends import mcp_tool_separator
from caliper.schema.results import (
    ObservedActivation,
    Outcome,
    OutcomeCounts,
    RunComparison,
    RunMeta,
    RunResults,
    TaskResult,
    UsageTotals,
    pass_at_k,
    pass_hat_k,
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
_SEP = "·" if _UNICODE else "-"
_RULE = "—" if _UNICODE else "-"
_WARN = "⚠" if _UNICODE else "!"
_CHECK = "✓" if _UNICODE else "+"
_CROSS = "✗" if _UNICODE else "x"
_TO = "→" if _UNICODE else "->"
_UNUSABLE = "⊘" if _UNICODE else "o"
_PENDING = "·" if _UNICODE else "."
_DOWN = "▼" if _UNICODE else "v"
_DELTA = "Δ" if _UNICODE else "delta"
# Re-exported: `list` marks an interrupted run with the same glyph, and a
# second literal would be a second thing to forget the ASCII fallback on.
UNUSABLE_GLYPH = _UNUSABLE
# Likewise: `list` renders an unmeasured run's score with the same rule the
# report uses for "nothing to show here".
RULE_GLYPH = _RULE
# And for `run`'s live warnings, which print beside the progress view.
WARN_GLYPH = _WARN
SEP_GLYPH = _SEP

# Per-outcome mark and style. Usable failures read as failures; the three noise
# outcomes share the distinct ⊘; a trigger probe is dim, not yellow: nothing was
# asked, so nothing went wrong.
_OUTCOME_MARK = {
    Outcome.PASS: (_CHECK, "green"),
    Outcome.TASK_FAIL: (_CROSS, "bold red"),
    Outcome.CHEAT: (_WARN, "bold yellow"),
    Outcome.INFRA_ERROR: (_UNUSABLE, "yellow"),
    Outcome.TIMEOUT: (_UNUSABLE, "yellow"),
    Outcome.JUDGE_ERROR: (_UNUSABLE, "yellow"),
    Outcome.NOT_CHECKED: (_RULE, "dim"),
}
_HARNESS_FAILURES = frozenset({Outcome.TIMEOUT, Outcome.INFRA_ERROR})

# Above this k the per-attempt marks collapse to a tally: ten spaced marks are
# wider than every other cell in the row put together.
_STRIP_MAX = 5
_TALLY_ORDER = [_CHECK, _CROSS, _WARN, _UNUSABLE, _RULE, _PENDING]
# The widest rate, "100.0%". Every rate is right-aligned in this slot so a
# column of them lines up on the percent sign.
_RATE_W = len("100.0%")


# ── small formatters ────────────────────────────────────────────────────────


def _fmt_score(score: float | None) -> str:
    """A rate with no false precision: `80%`, `66.7%`, or `—` when unmeasured."""
    if score is None:
        return _RULE
    pct = score * 100
    return f"{pct:.0f}%" if abs(pct - round(pct)) < 0.05 else f"{pct:.1f}%"


def _pp(delta: float | None, regression: bool | None = None) -> Text:
    """A rate delta in percentage points: 60% → 100% is +40 pp, not +40%.

    Red when it is a regression. Unmeasured (None) and unchanged (0) both read
    "—"; the JSON keeps them distinct.
    """
    if delta is None or abs(delta) < 1e-9:
        return Text(_RULE, style="dim")
    pct = delta * 100
    num = f"{pct:+.0f}" if abs(pct - round(pct)) < 0.05 else f"{pct:+.1f}"
    worse = delta < 0 if regression is None else regression
    return Text(f"{num} pp", style="red" if worse else "green")


def _relative(
    before: float, after: float, fmt: Callable[[float], str] | None = None
) -> Text:
    """A cost delta, relative: green when cheaper, red when costlier. This
    NEVER flips has_regression (docs/CONTEXT.md → Regression).

    From a zero baseline there is no percentage, but a new cost is still a
    cost: it is shown absolute (`+500`) through ``fmt`` rather than as the
    dash that means "unchanged".
    """
    delta = after - before
    if abs(delta) < 1e-9:
        return Text(_RULE, style="dim")
    if before == 0:
        if fmt is None:
            return Text(_RULE, style="dim")
        return Text(f"+{fmt(after)}", style="red")
    sign = "+" if delta > 0 else "-"
    return Text(
        f"{sign}{abs(delta / before) * 100:.0f}%",
        style="red" if delta > 0 else "green",
    )


def _rate_style(score: float | None) -> str:
    """One colour rule for every per-task rate: full is plain, partial yellow,
    zero red."""
    if score is None or score >= 0.99:
        return ""
    return "bold red" if score == 0 else "yellow"


def _engine_label(backend: str | None, model: str | None) -> str:
    """`backend · model` when a model is known, else just the backend."""
    label = backend or "?"
    return f"{label} {_SEP} {model}" if model else label


def _engine_text(backend: str | None, model: str | None) -> Text:
    return Text(_engine_label(backend, model), style="cyan")


# ── marks: one attempt, one glyph ───────────────────────────────────────────


def _marks(items: list[tuple[str, str]], k: int) -> Text:
    """Per-attempt marks for k <= 5, padded with `·` for attempts that never ran;
    a tally (`✓7 ✗3`) above that.

    Marks are spaced so a glyph drawn wider than its cell (notably ⊘, in some
    fonts) cannot collide with its neighbour. Passes are dimmed in the strip so
    the failures are what the eye lands on.
    """
    items = list(items) + [(_PENDING, "dim")] * max(0, k - len(items))
    text = Text()
    if k <= _STRIP_MAX:
        for i, (glyph, style) in enumerate(items):
            if i:
                text.append(" ")
            text.append(glyph, style="dim green" if glyph == _CHECK else style)
        return text
    counts: dict[str, tuple[int, str]] = {}
    for glyph, style in items:
        n, _ = counts.get(glyph, (0, style))
        counts[glyph] = (n + 1, style)
    for glyph in _TALLY_ORDER:
        if glyph in counts:
            n, style = counts[glyph]
            if len(text):
                text.append(" ")
            text.append(f"{glyph}{n}", style=style)
    return text


def _outcome_marks(outcomes: list[Outcome], k: int) -> Text:
    return _marks([_OUTCOME_MARK.get(o, (_CROSS, "bold red")) for o in outcomes], k)


def _activation_marks(tr: TaskResult, k: int) -> Text:
    """The activation verdict of each attempt. An inadmissible attempt (a
    truncated transcript) is ⊘, never a fabricated miss (docs/CONTEXT.md →
    Activation admissibility)."""
    items = []
    for attempt in tr.attempts:
        if not attempt.outcome.is_activation_usable:
            items.append((_UNUSABLE, "yellow"))
        elif attempt.activation_passed is True:
            items.append((_CHECK, "green"))
        elif attempt.activation_passed is False:
            items.append((_CROSS, "bold red"))
        else:
            items.append((_RULE, "dim"))
    return _marks(items, k)


def _rate_cell(score: float | None, evidence: Text | str, style: str = "") -> Text:
    """The one rate cell: the rate in its fixed slot, then what it rests on."""
    cell = Text(
        _fmt_score(score).rjust(_RATE_W),
        style=style or ("dim" if score is None else ""),
    )
    cell.append("  ")
    if isinstance(evidence, Text):
        cell.append_text(evidence)
    else:
        cell.append(evidence, style="dim")
    return cell


def _empty_rate(label: str = "") -> Text:
    cell = Text(_RULE.rjust(_RATE_W), style="dim")
    if label:
        cell.append(f"  {label}", style="dim")
    return cell


def _avg_detail(avg: float, successes: int, usable: int, tasks: int) -> str:
    """`16/20 ✓` when the pooled count says the same thing as the average.

    The average is a mean of per-task rates (docs/adr/0007), so with unequal
    denominators the pooled count can disagree with it. Two numbers that
    disagree make the reader pick one, so the detail then names what was
    averaged instead.
    """
    if usable and abs(successes / usable - avg) < 0.0005:
        return f"{successes}/{usable} {_CHECK}"
    return f"avg of {tasks}"


def _two_line(top: str, bottom: str) -> Text:
    return Text.assemble((f"{top}\n", "dim"), bottom)


# ── header ──────────────────────────────────────────────────────────────────


def _badge(command: str, spec: Text, k: Text, when: str = "") -> Text:
    line = Text.assemble(
        (" CALIPER ", "bold black on cyan"), "  ", (command, "bold"), "  "
    )
    line.append_text(spec)
    line.append("   k=", style="dim")
    line.append_text(k)
    if when:
        line.append(f"   {when}", style="dim")
    return line


def _names(names: list[str], removed: list[str] = ()) -> Text:
    """A comma list, with removed members struck through and marked."""
    text = Text()
    for name in names:
        if len(text):
            text.append(", ", style="dim")
        text.append(name)
    for name in removed:
        if len(text):
            text.append(", ", style="dim")
        text.append(name, style="dim strike")
        text.append(" removed", style="yellow")
    if not len(text):
        text.append("none", style="dim")
    return text


def _setup_text(run: RunMeta) -> Text:
    """Whether the attempts loaded the user's own setup, and what it brought
    in (docs/adr/0028). Said in full both ways: "isolated" alone left a reader
    asking isolated from what."""
    if not run.user_customizations:
        return Text("without user customizations", style="dim")
    text = Text("with user customizations", style="yellow")
    loaded = run.loaded_user_customizations
    if loaded is None:
        text.append("  not listed by this backend", style="dim")
    elif not loaded:
        text.append("  none found", style="dim")
    else:
        text.append(f"  {', '.join(loaded)}")
    return text


def _env_grid() -> Table:
    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="dim", no_wrap=True)
    grid.add_column()
    return grid


def _judged(results: RunResults) -> bool:
    """Whether an LLM judge graded any attempt (docs/adr/0033)."""
    return any(
        attempt.judge_seconds is not None
        for task in results.task_results
        for attempt in task.attempts
    )


def print_banner(
    spec_name: str, k: int, backend: str, model: str | None = None
) -> None:
    """The line `run` prints before its first attempt.

    Only what is known before the environment resolves. The full header —
    skills, servers, setup — comes with the report, once they are facts.
    """
    console.print()
    console.print(
        _badge("run", Text(spec_name, style="bold"), Text(str(k), style="cyan"))
        .append("   ")
        .append_text(_engine_text(backend, model))
    )


def _print_run_header(results: RunResults) -> None:
    run = results.run
    console.print()
    console.print(
        _badge(
            "run",
            Text(run.spec, style="bold"),
            Text(str(run.k), style="cyan"),
            run.timestamp.strftime("%Y-%m-%d %H:%M"),
        )
    )
    grid = _env_grid()
    grid.add_row("engine", _engine_text(run.backend, run.model))
    # Only when a judge actually ran: an assert-only run would otherwise name a
    # grader that never graded anything.
    if _judged(results) and run.judge_backend:
        same = run.judge_backend == run.backend and run.judge_model in (
            None,
            run.model,
        )
        grid.add_row(
            "judge",
            Text("same as engine", style="dim")
            if same
            else _engine_text(run.judge_backend, run.judge_model or "default model"),
        )
    skills = [s.name for s in results.skill_snapshots if s.name]
    if skills or run.ablated_skills:
        grid.add_row("skills", _names(skills, run.ablated_skills))
    if run.mcp_servers or run.ablated_servers:
        grid.add_row("mcp", _names(list(run.mcp_servers or []), run.ablated_servers))
    grid.add_row("setup", _setup_text(run))

    # Status rows: what a reader must know before believing a single number.
    status: list[Text] = []
    # An ablated run's numbers are only readable next to what was removed, named
    # as the invocation named it. When a skill was removed its activation column
    # is skipped by design — said once, up front, rather than leaving a reader
    # to wonder why the verdicts went blank. A removed server leaves the
    # verdicts intact, so it gets the marker without that note.
    if run.ablated:
        line = Text.assemble(
            (f"without {', '.join(run.ablated)}", "yellow"),
            ("  via --ablate", "dim"),
        )
        if run.ablated_skills:
            line.append(f" {_SEP} activation observed, not scored", style="dim")
        status.append(line)
    if results.task_results and all(t.trigger_only for t in results.task_results):
        status.append(
            Text.assemble(
                ("trigger probes only", "cyan"),
                ("  no execution checks · activation is the score", "dim"),
            )
        )
    # A short sample is the one thing a reader must not mistake for a full one.
    if run.interrupted:
        ran = sum(len(t.attempts) for t in results.task_results)
        asked = run.k * len(results.task_results)
        status.append(
            Text.assemble(
                ("interrupted", "yellow"),
                (f"  {ran} of {asked} attempts ran · scored over those", "dim"),
            )
        )
    if run.hook_failures:
        n = len(run.hook_failures)
        status.append(
            Text.assemble(
                (f"{n} lifecycle hook{'s' if n > 1 else ''} failed", "bold red"),
                ("  see notes", "dim"),
            )
        )
    for i, line in enumerate(status):
        grid.add_row("status" if i == 0 else "", line)
    console.print(grid)


# ── notes ───────────────────────────────────────────────────────────────────


@dataclass
class _Note:
    """One closing line. ``level`` sorts and styles it: 0 red, 1 yellow, 2 dim."""

    level: int
    glyph: str
    label: str
    body: str | Text = ""
    aside: str = ""


_NOTE_STYLES = {
    0: ("bold red", "bold red"),
    1: ("yellow", "bold yellow"),
    2: ("dim", "bold"),
}


def _print_notes(notes: list[_Note]) -> None:
    if not notes:
        return
    console.print()
    for note in sorted(notes, key=lambda n: n.level):
        glyph_style, label_style = _NOTE_STYLES[note.level]
        # A grid, so a long note wraps under its own text rather than under the
        # glyph.
        grid = Table.grid(padding=(0, 1))
        grid.add_column(no_wrap=True)
        grid.add_column(ratio=1)
        # Styled as a span, not as the line's base style, which the body
        # would inherit.
        line = Text.assemble((note.label, label_style))
        if note.body:
            line.append("  ")
            line.append_text(
                note.body if isinstance(note.body, Text) else Text(note.body)
            )
        if note.aside:
            line.append(f"  {note.aside}", style="dim")
        grid.add_row(Text(f" {note.glyph}", style=glyph_style), line)
        console.print(grid)


# Each caliper warning is one sentence, `what — why/fix`, mirrored into the
# JSON. The header already shows *what* differs, so where it does, the note
# leads with a short name and keeps the why. Display only: --format json keeps
# the full sentence.
_WARNING_HEADS = (
    ("different skill neighbourhoods", "skills differ"),
    ("different MCP servers", "MCP servers differ"),
    ("different judges", "judges differ"),
    ("different user customizations", "setups differ"),
    ("comparing different specs", "specs differ"),
)


def _warning_note(warning: str) -> _Note:
    head, _, tail = warning.partition(" — ")
    for prefix, short in _WARNING_HEADS:
        if head.startswith(prefix):
            head = short
    return _Note(1, _WARN, head, "", tail)


# ── caliper run (live) ──────────────────────────────────────────────────────


class _MarksColumn(ProgressColumn):
    """The task's marks so far, in the glyphs the final table uses."""

    def render(self, task: Task) -> Text:
        return task.fields.get("marks") or Text("")


def make_progress(tasks: list[str], k: int) -> tuple[Progress, dict[str, TaskID]]:
    progress = Progress(
        SpinnerColumn(finished_text=" "),
        TextColumn(
            "{task.description}",
            justify="left",
            table_column=Column(width=40, overflow="ellipsis", no_wrap=True),
        ),
        _MarksColumn(table_column=Column(no_wrap=True)),
        TimeElapsedColumn(),
        console=console,
        expand=False,
        transient=True,
    )
    task_ids: dict[str, TaskID] = {}
    for name in tasks:
        task_ids[name] = progress.add_task(name, total=k, marks=_marks([], k))
    return progress, task_ids


def update_progress(
    progress: Progress,
    task_ids: dict[str, TaskID],
    task_name: str,
    k: int,
    counts: OutcomeCounts | None = None,
    finished: bool = False,
    by_attempt: dict[int, Outcome] | None = None,
) -> None:
    """Advance one task's row.

    ``by_attempt`` places each outcome at its attempt number. Attempts finish
    in parallel and out of order, and without it a fast attempt 2 would take
    attempt 1's slot — so the live marks would disagree with the numbered
    report. When given it is the only source: the completed count is read from
    the same snapshot as the marks, so the two cannot disagree. Without it,
    ``counts`` gives marks in completion order.
    """
    tid = task_ids.get(task_name)
    if tid is None:
        return
    # One snapshot: worker threads keep adding outcomes, and the marks and the
    # completed count must come from the same ones.
    if by_attempt is not None:
        placed = dict(by_attempt)
        completed = len(placed)
        items = [
            _OUTCOME_MARK.get(placed[n], (_CROSS, "bold red"))
            if n in placed
            else (_PENDING, "dim")
            for n in range(1, k + 1)
        ]
        marks = _marks(items, k)
    else:
        outcomes = list(counts.outcomes) if counts is not None else []
        completed = len(outcomes)
        marks = _outcome_marks(outcomes, k)
    progress.update(
        tid,
        total=k,
        completed=k if finished and completed < k else completed,
        marks=marks,
    )


# ── caliper run (report) ────────────────────────────────────────────────────


def _task_name_cell(tr: TaskResult, k: int) -> Text:
    name = Text(
        tr.task_name,
        style="dim" if tr.score is None and not tr.trigger_only else "",
    )
    if any(attempt.hook_failures for attempt in tr.attempts):
        name.append(f"  {_UNUSABLE} hook", style="bold red")
    if tr.aborted(k):
        name.append("  aborted", style="yellow")
    return name


def _success_cell(tr: TaskResult, k: int) -> Text:
    # A trigger probe has no execution numbers: "0/3" would read as three
    # failures rather than three questions never asked.
    if tr.trigger_only:
        return _empty_rate("probe")
    return _rate_cell(
        tr.score,
        _outcome_marks([a.outcome for a in tr.attempts], k),
        _rate_style(tr.score),
    )


def _activation_cell(tr: TaskResult, k: int) -> Text:
    """Did this task's activation claim hold, attempt by attempt? Dim "—" when it
    claimed nothing. Counted over activation-usable attempts only, so a task
    whose attempts all timed out reads "—", not a confident 0%."""
    if tr.activation_expected is None:
        return _empty_rate()
    score = tr.activation_score
    return _rate_cell(score, _activation_marks(tr, k), _rate_style(score))


def _cost_cell(tokens: float | None, wall: str, style: str) -> Text:
    """Tokens and wall in one column, both right-aligned."""
    shown = _fmt_tokens(int(tokens)) if tokens is not None else _RULE
    return Text(shown.rjust(5) + wall.rjust(8), style=style)


def print_results(results: RunResults, verbose: bool = False) -> None:
    run, k, agg = results.run, results.run.k, results.aggregate
    tasks = results.task_results
    usage = results.usage
    _print_run_header(results)
    console.print()

    show_success = not tasks or not all(t.trigger_only for t in tasks)
    # A removed *skill* drops every expectation (docs/adr/0015), so the column
    # would be all dashes; the skills table shows what was observed instead.
    show_activation = (
        any(t.activation_expected is not None for t in tasks) and not run.ablated_skills
    )

    table = Table(box=box.ROUNDED, header_style="bold cyan")
    table.add_column("Task")
    # Only the task name may wrap: a wrapped rate cell splits its marks.
    if show_success:
        table.add_column("success", no_wrap=True)
    if verbose:
        table.add_column("pass@k", justify="right", no_wrap=True)
        table.add_column("pass^k", justify="right", no_wrap=True)
    if show_activation:
        table.add_column("activation", no_wrap=True)
    table.add_column("cost", justify="right", no_wrap=True)

    for tr in tasks:
        row: list[Text] = [_task_name_cell(tr, k)]
        if show_success:
            row.append(_success_cell(tr, k))
        if verbose:
            row += [
                Text(_fmt_score(tr.pass_at_k), style="dim"),
                Text(_fmt_score(tr.pass_hat_k), style="dim"),
            ]
        if show_activation:
            row.append(_activation_cell(tr, k))
        own = tr.usage
        row.append(
            _cost_cell(
                own.total_tokens if own.tokens_reported else None,
                _fmt_duration(own.wall_seconds),
                "dim",
            )
        )
        table.add_row(*row)

    table.add_section()
    overall: list[Text] = [Text("Overall", style="bold")]
    per_attempt: list[Text] = [Text("per attempt", style="dim")]
    if show_success:
        if agg.measured:
            scored = [t for t in tasks if t.score is not None]
            overall.append(
                _rate_cell(
                    agg.avg_score,
                    _avg_detail(
                        agg.avg_score,
                        sum(t.successes for t in scored),
                        sum(t.usable for t in scored),
                        agg.scored_tasks,
                    ),
                    "bold green" if agg.avg_score >= 0.99 else "bold",
                )
            )
        else:
            # Nothing measured: "0%" would read as total failure of a run where
            # nothing failed.
            overall.append(_empty_rate("no execution checks"))
        per_attempt.append(Text(""))
    if verbose:
        overall += [Text(""), Text("")]
        per_attempt += [Text(""), Text("")]
    if show_activation:
        if agg.avg_activation_score is None:
            overall.append(_empty_rate())
        else:
            asserted = [t for t in tasks if t.activation_score is not None]
            overall.append(
                _rate_cell(
                    agg.avg_activation_score,
                    _avg_detail(
                        agg.avg_activation_score,
                        sum(t.activation_successes for t in asserted),
                        sum(t.activation_usable for t in asserted),
                        len(asserted),
                    ),
                    "bold green" if agg.avg_activation_score >= 0.99 else "bold",
                )
            )
        per_attempt.append(Text(""))
    overall.append(
        _cost_cell(
            usage.total_tokens if usage.tokens_reported else None,
            _fmt_duration(usage.wall_seconds),
            "bold",
        )
    )
    # Per attempt over the usable ones, tokens and wall alike: an unusable
    # attempt's spend is reported in its own note, not averaged in.
    usable = usage.usable_attempts
    per_attempt.append(
        _cost_cell(
            (usage.total_tokens - usage.unusable_tokens) / usable
            if usage.tokens_reported and usable
            else None,
            f"{usage.usable_wall_seconds / usable:.1f}s" if usable else _RULE,
            "dim",
        )
    )
    table.add_row(*overall)
    table.add_row(*per_attempt)
    console.print(table)

    _print_skills(results)
    _print_mcp(results)
    _print_notes(_run_notes(results))

    detailed = tasks if verbose else [t for t in tasks if _needs_detail(t)]
    if detailed:
        console.print()
    for tr in detailed:
        console.print(_task_panel(tr, k, verbose, ablated=bool(run.ablated_skills)))
    console.print()


def _activation_severity(stats) -> float:
    """Sort key: a skill's worst failure rate, worst first (descending).

    Both directions are converted to "how wrong is this" so they compare on one
    scale. A missing rate means the case never arose, which is not a failure, so
    it contributes nothing rather than counting as total failure.
    """
    missed = 1.0 - stats.recall if stats.recall is not None else 0.0
    over = stats.unwanted_rate if stats.unwanted_rate is not None else 0.0
    return -max(missed, over)


def _skill_rate_cell(
    numerator: int, denominator: int, rate: float | None, *, higher_is_better=True
) -> Text:
    """`75%  3/4`, dim "—" when the case never arose.

    A skill nothing ever expected has no fire-when-wanted rate, and one that
    never faced a prompt it should skip has no chance to over-fire. Neither is
    a zero. ``higher_is_better`` flips the colouring for the over-firing
    column, where the good value is 0%.
    """
    if rate is None or denominator <= 0:
        return _empty_rate()
    good = rate >= 0.99 if higher_is_better else rate <= 0.01
    return _rate_cell(
        rate, f"{numerator}/{denominator}", "green" if good else "bold red"
    )


def _print_skills(results: RunResults) -> None:
    """The activation half, on the *skill* axis (docs/CONTEXT.md → Activation
    score). Rendered only when the spec asserted ``activates:``, or as the
    observation of a skill-ablated run."""
    run, agg = results.run, results.aggregate
    if run.ablated_skills:
        _print_observed_skills(results)
        return
    if agg.avg_activation_score is None:
        return
    # A skill never wanted and never seen carries no measurement, only the fact
    # that it was installed: it gets a note, not an empty row.
    measured = [s for s in agg.activation_per_skill if s.expected or s.fired]
    if not measured:
        return
    console.print()
    console.print(
        Text.assemble(
            (" Skills", "bold"), ("  activation per skill · worst first", "dim")
        )
    )
    table = Table(box=box.ROUNDED, header_style="bold cyan")
    table.add_column("Skill")
    table.add_column("wanted", justify="right")
    # The same verb on both sides, so the pair reads as one behaviour measured
    # over two populations. The second is good-when-*low*.
    table.add_column("fires when wanted")
    table.add_column("fires when not wanted")
    for stats in sorted(measured, key=_activation_severity):
        table.add_row(
            stats.skill,
            Text(f"{stats.expected} of {stats.total}", style="dim"),
            _skill_rate_cell(stats.hits, stats.expected, stats.recall),
            _skill_rate_cell(
                stats.unwanted,
                stats.opportunities,
                stats.unwanted_rate,
                higher_is_better=False,
            ),
        )
    console.print(table)


def _print_observed_skills(results: RunResults) -> None:
    """What the surviving skills reached for on a skill-ablated run.

    The run withholds the activation *verdict* but keeps the observation
    (docs/adr/0015-ablation-names-its-subject-at-the-invocation.md). Without
    this, "with the parent removed, did its neighbours pick up the work?" — the
    whole reason a partial ablation is interesting — would be invisible.
    """
    rows = ObservedActivation.from_task_results(
        results.task_results, [s.name for s in results.skill_snapshots if s.name]
    )
    if not rows or not rows[0].observed:
        return
    console.print()
    console.print(
        Text.assemble(
            (" Skills", "bold"),
            ("  observed only · nothing is asserted on an ablated run", "dim"),
        )
    )
    table = Table(box=box.ROUNDED, header_style="bold cyan")
    table.add_column("Skill")
    table.add_column("fired")
    for row in rows:
        style = "" if row.fired else "dim"
        table.add_row(
            Text(row.skill, style=style),
            _rate_cell(row.fired / row.observed, f"{row.fired}/{row.observed}", style),
        )
    for name in results.run.ablated_skills:
        table.add_row(
            Text(name, style="dim strike"),
            Text("removed".rjust(_RATE_W + 2), style="yellow"),
        )
    console.print(table)


def _mcp_calls(results: RunResults) -> dict[str, tuple[int, int, int]]:
    """Per server: (attempts that called it, attempts observed, total calls).

    Read from the saved transcripts. claude-code and codex name a call
    ``mcp__<server>__<tool>``, hermes ``mcp_<server>_<tool>``
    (docs/CONTEXT.md → MCP server (declared)). A server loaded from the user's
    own setup is counted too: it competes for the same work.
    """
    declared = list(results.run.mcp_servers or [])
    loaded = results.run.loaded_user_customizations or []
    user = [n.split(":", 1)[1] for n in loaded if n.startswith("mcp:")]
    servers = declared + [s for s in user if s not in declared]
    stats = {server: [0, 0, 0] for server in servers}
    for task in results.task_results:
        for attempt in task.attempts:
            # An attempt with no transcript, or a truncated one, can't say a
            # server went unused.
            if attempt.transcript is None or not attempt.outcome.is_activation_usable:
                continue
            calls: dict[str, int] = {}
            for turn in attempt.transcript:
                # The invocation only: hermes also names the tool on its result
                # turn, which would count one call twice.
                if turn.role != "tool_use" or not turn.tool_name:
                    continue
                server = _mcp_server(turn.tool_name, servers, results.run.backend)
                if server is not None:
                    calls[server] = calls.get(server, 0) + 1
            for server in servers:
                stats[server][1] += 1
                if calls.get(server):
                    stats[server][0] += 1
                    stats[server][2] += calls[server]
    return {server: tuple(counts) for server, counts in stats.items()}


def _mcp_server(tool_name: str, servers: list[str], backend: str) -> str | None:
    """The one known server a tool call belongs to, or ``None``.

    The separator is the producing backend's (caliper/backends.py), never
    guessed from the name: hermes writes ``mcp_<server>_<tool>``, claude-code and codex
    ``mcp__<server>__<tool>`` (docs/CONTEXT.md → MCP server (declared)), and a
    server name may itself contain ``_`` or ``__`` — so ``mcp__mail_read`` is
    hermes calling ``_mail``, or claude-code calling ``mail_read``'s server.
    Within one form the longest known server wins: ``mcp_mail_archive_read``
    starts with both ``mail_`` and ``mail_archive_``.
    """
    sep = mcp_tool_separator(backend)
    matches = [s for s in servers if tool_name.startswith(f"mcp{sep}{s}{sep}")]
    return max(matches, key=len) if matches else None


def _print_mcp(results: RunResults) -> None:
    """Which servers the agent actually used. Shown, never scored."""
    stats = _mcp_calls(results)
    removed = results.run.ablated_servers
    if not any(observed for _, observed, _ in stats.values()) and not removed:
        return
    declared = set(results.run.mcp_servers or [])
    console.print()
    console.print(
        Text.assemble(
            (" MCP servers", "bold"), ("  tool calls, from transcripts", "dim")
        )
    )
    table = Table(box=box.ROUNDED, header_style="bold cyan")
    table.add_column("Server")
    table.add_column("called in")
    table.add_column("calls", justify="right")
    for server, (used, observed, calls) in stats.items():
        name = Text(server, style="" if used else "dim")
        if server not in declared:
            name.append("  user", style="yellow")
        table.add_row(
            name,
            _rate_cell(
                used / observed if observed else None,
                f"{used}/{observed} attempts",
                "" if used else "dim",
            ),
            Text(str(calls), style="" if calls else "dim"),
        )
    for server in removed:
        table.add_row(
            Text(server, style="dim strike"),
            Text("removed".rjust(_RATE_W + 2), style="yellow"),
            Text(""),
        )
    console.print(table)


def _run_notes(results: RunResults) -> list[_Note]:
    run, agg, usage = results.run, results.aggregate, results.usage
    notes: list[_Note] = []
    names = {t.task_id: t.task_name for t in results.task_results}
    for failure in run.hook_failures:
        # A cancelled attempt has no record, so its hook failure lives only at
        # run level and no panel will show its output: the note carries it.
        in_panel = any(
            failure in attempt.hook_failures
            for task in results.task_results
            if task.task_id == failure.task_id
            for attempt in task.attempts
        )
        output = failure.output.strip()
        if len(output) > _OUTPUT_TRUNCATE_AT:
            output = "…" + output[-_OUTPUT_TRUNCATE_AT:]
        notes.append(
            _Note(
                0,
                _UNUSABLE,
                f"{failure.phase} hook exited {failure.exit_code}",
                f"{names.get(failure.task_id, failure.task_id)} {_SEP} "
                f"attempt {failure.attempt}",
                "output in the task's panel" if in_panel else output or "no output",
            )
        )
    cheats = [t.task_name for t in results.task_results if t.any_cheat]
    if cheats:
        notes.append(
            _Note(
                0,
                _WARN,
                f"{len(cheats)} cheat{'s' if len(cheats) > 1 else ''}",
                ", ".join(cheats),
                "a forbidden file was read · counted as a failure",
            )
        )
    noise = results.noise_counts
    if noise:
        breakdown = f" {_SEP} ".join(f"{n} {o.value}" for o, n in noise.items())
        spend = []
        if usage.unusable_tokens:
            spend.append(f"{_fmt_tokens(usage.unusable_tokens)} tokens")
        spend.append(_fmt_duration(usage.unusable_wall_seconds))
        notes.append(
            _Note(
                1,
                _UNUSABLE,
                f"{sum(noise.values())} unusable",
                breakdown,
                f"excluded from the score {_SEP} spent {', '.join(spend)}",
            )
        )
    # Only when something was actually retried: the common case stays quiet.
    if usage.retried_attempts:
        plural = "s" if usage.retried_attempts > 1 else ""
        notes.append(
            _Note(
                1,
                _WARN,
                "throttled",
                f"{usage.retries} retries across {usage.retried_attempts} attempt{plural}",
                "wall excludes the waiting",
            )
        )
    # Shown whether or not anything was asserted, and never scored
    # (docs/CONTEXT.md → Built-in skill): without it, a built-in skill winning
    # the prompt reads the same as nothing firing.
    builtin = ObservedActivation.builtin_from_task_results(results.task_results)
    if builtin:
        notes.append(
            _Note(
                2,
                _SEP,
                "built-in skills",
                ", ".join(f"{b.skill} {b.fired}/{b.observed}" for b in builtin),
                f"ship with {run.backend} {_SEP} shown, not scored",
            )
        )
    # A dormant neighbour is itself an answer: the probes never exercised it.
    if agg.avg_activation_score is not None and not run.ablated_skills:
        dormant = [
            s.skill for s in agg.activation_per_skill if not (s.expected or s.fired)
        ]
        if dormant:
            notes.append(
                _Note(
                    2,
                    _SEP,
                    "never wanted, never fired",
                    ", ".join(dormant),
                    "no probe exercises them",
                )
            )
    if usage.tokens_reported:
        notes.append(
            _Note(
                2,
                _SEP,
                "tokens",
                f"{_fmt_tokens(usage.prompt_tokens)} in / "
                f"{_fmt_tokens(usage.output_tokens)} out",
            )
        )
    # Only when a judge ran: an assert-only run would otherwise print a
    # confident "0s", which reads as a free judge rather than no judge.
    if usage.judged_attempts:
        per = usage.judge_seconds / usage.judged_attempts
        notes.append(
            _Note(
                2,
                _SEP,
                "judge time",
                f"{_fmt_duration(usage.judge_seconds)} {_SEP} {per:.1f}s per graded attempt",
                "not in wall",
            )
        )
    return notes


_OUTPUT_TRUNCATE_AT = 500


def _format_output(output: str) -> str:
    """Raw agent or hook output as safe markup, keeping only its tail if long.

    Truncates *before* escaping: cutting an escaped string could keep a tag and
    drop the backslash that shields it, and Rich would then parse the tag.
    """
    if not output or not output.strip():
        return r"[dim]\[no output][/dim]"
    if len(output) > _OUTPUT_TRUNCATE_AT:
        tail = escape(output[-_OUTPUT_TRUNCATE_AT:])
        return (
            rf"[dim]\[...truncated, showing last {_OUTPUT_TRUNCATE_AT} chars][/dim]"
            f"\n{tail}"
        )
    return escape(output)


def _needs_detail(tr: TaskResult) -> bool:
    """Whether a task earns a panel: either scoreboard came up short.

    A trigger-only task is judged solely on activation — its ``score`` is
    ``None`` by construction, and treating that as "didn't fully pass" would
    print a panel for every correct trigger probe.
    """
    if any(attempt.hook_failures for attempt in tr.attempts):
        return True
    activation_short = tr.activation_score is not None and tr.activation_score < 1.0
    if tr.trigger_only:
        return activation_short
    return tr.score is None or tr.score < 1.0 or activation_short


def _attempt_mark(attempt) -> tuple[str, str]:
    """The attempt's verdict mark. A trigger probe's attempt checked nothing but
    activation, so that verdict is the one to show."""
    if attempt.outcome == Outcome.NOT_CHECKED and attempt.activation_passed is not None:
        return _OUTCOME_MARK[
            Outcome.PASS if attempt.activation_passed else Outcome.TASK_FAIL
        ]
    return _OUTCOME_MARK.get(attempt.outcome, (_CROSS, "bold red"))


def _task_panel(tr: TaskResult, k: int, verbose: bool, ablated: bool) -> Panel:
    """Every attempt of one task; the detail only where something went wrong.

    A two-column grid rather than pre-indented lines, so a long output or judge
    note wraps under its own column instead of back at the border.
    """
    grid = Table.grid(padding=(0, 2))
    grid.add_column(no_wrap=True)
    grid.add_column(overflow="fold")
    if tr.aborted(k):
        grid.add_row(
            Text("aborted", style="yellow"), f"after {len(tr.attempts)} of {k} attempts"
        )
    # What the judge was asked, for debugging a verdict. Only under --verbose:
    # the default panel is for spotting a failure, not re-reading the spec.
    if verbose and tr.expect:
        grid.add_row(Text("expect", style="dim"), Text(tr.expect))
    # A red activation row is unreadable without the claim it broke. An ablated
    # run dropped the claim (docs/adr/0015), so it is not repeated there.
    if tr.activation_expected is not None and not ablated:
        expected = ", ".join(tr.activation_expected) or "nothing (silence)"
        grid.add_row(Text("should activate", style="dim"), Text(expected))
    for attempt in tr.attempts:
        glyph, style = _attempt_mark(attempt)
        meta = Text(f"{attempt.duration_seconds:.1f}s", style="dim")
        if attempt.usage is not None and attempt.usage.total_tokens:
            meta.append(
                f" {_SEP} {_fmt_tokens(attempt.usage.total_tokens)} tokens", style="dim"
            )
        if not (attempt.outcome.is_usable or attempt.outcome == Outcome.NOT_CHECKED):
            meta = Text.assemble((attempt.outcome.value, "yellow"), "  ", meta)
        grid.add_row(Text.assemble((glyph, style), f" attempt {attempt.attempt}"), meta)

        # A clean attempt is one line: its output is what passing looks like,
        # and repeating it k times buries the attempt that failed.
        clean = (
            attempt.outcome in (Outcome.PASS, Outcome.NOT_CHECKED)
            and attempt.activation_passed is not False
            and not attempt.hook_failures
        )
        if clean and not verbose:
            continue
        for evidence in attempt.cheat_evidence:
            grid.add_row(Text("    cheat", style="yellow"), Text(evidence))
        for failure in attempt.hook_failures:
            detail = Text(f"exited {failure.exit_code}")
            if failure.output.strip():
                detail.append(f" {_SEP} ")
                detail.append_text(Text.from_markup(_format_output(failure.output)))
            grid.add_row(Text(f"    {failure.phase} hook", style="red"), detail)
        if attempt.activation_passed is False:
            reached = ", ".join(attempt.activated or []) or "nothing"
            grid.add_row(Text("    activated", style="red"), Text(reached))
        elif attempt.activation_observed and attempt.activation_passed is None:
            # Nothing was asserted, so this is informational only.
            reached = ", ".join(attempt.activated or []) or "nothing"
            grid.add_row(Text("    activated", style="dim"), Text(reached, style="dim"))
        elif attempt.activated and attempt.outcome in _HARNESS_FAILURES:
            # A timed-out or failed attempt keeps what it saw load before it
            # stopped. Not graded, but it is the first thing to look at.
            grid.add_row(
                Text("    activated so far", style="dim"),
                Text(", ".join(attempt.activated), style="dim"),
            )
        if attempt.builtin_activated:
            grid.add_row(
                Text("    built-in", style="dim"),
                Text(", ".join(attempt.builtin_activated), style="dim"),
            )
        grid.add_row(
            Text("    output", style="dim"),
            Text.from_markup(_format_output(attempt.output)),
        )
        if attempt.assert_evidence:
            # A timeout or infra failure stores the harness's error here, not an
            # assertion's.
            label = "error" if attempt.outcome in _HARNESS_FAILURES else "assert"
            grid.add_row(
                Text(f"    {label}", style="dim"),
                Text(attempt.assert_evidence, style="dim"),
            )
        if attempt.autorater_reasoning:
            grid.add_row(
                Text("    judge", style="dim"),
                Text(attempt.autorater_reasoning, style="dim"),
            )
        if verbose and attempt.autorater_script:
            grid.add_row(
                Text("    judge script", style="dim"),
                Text(attempt.autorater_script, style="dim"),
            )

    if tr.trigger_only:
        score, marks, border = (
            tr.activation_score,
            _activation_marks(tr, k),
            "bright_black",
        )
    else:
        score = tr.score
        marks = _outcome_marks([a.outcome for a in tr.attempts], k)
        hooks = any(a.hook_failures for a in tr.attempts)
        if score == 0 or hooks or tr.any_cheat:
            border = "red"
        elif score is None or score < 0.99 or tr.unusable:
            border = "yellow"
        else:
            border = "bright_black"
    title = Text.assemble((f" {tr.task_name} ", "bold"), (f" {_fmt_score(score)} ", ""))
    title.append_text(marks)
    title.append(" ")
    if tr.trigger_only:
        title.append("  trigger probe ", style="dim")
    return Panel(
        grid, title=title, title_align="left", border_style=border, padding=(0, 1)
    )


# ── caliper compare ─────────────────────────────────────────────────────────


def _side_heads(comp: RunComparison) -> tuple[str, str]:
    """An ablation pair is titled from the marker; two plain runs are A and B."""
    if comp.a_label or comp.b_label:
        return comp.a_label or "A", comp.b_label or "B"
    return "A", "B"


def _env_row(
    grid: Table, key: str, a: Text, b: Text, differs: bool, style: str
) -> None:
    if not differs:
        grid.add_row(key, a, Text("same", style="dim"))
        return
    b = b.copy()
    b.stylize(style)
    grid.add_row(Text(key, style=style), a, b)


def _side_names(mine: list[str], theirs: list[str], style: str) -> Text:
    """A comma list whose members missing from the other side are highlighted."""
    text = Text()
    for name in mine:
        if len(text):
            text.append(", ", style="dim")
        text.append(name, style=style if name not in theirs else "")
    if not len(text):
        text.append("none", style="dim")
    return text


def _print_compare_header(comp: RunComparison) -> None:
    a, b = comp.a, comp.b
    spec = Text(a.spec, style="bold")
    if comp.spec_mismatch:
        spec.append(f" {_TO} ", style="dim")
        spec.append(b.spec, style="bold yellow")
    k = Text(str(a.k), style="cyan")
    if comp.k_mismatch:
        k.append(f" {_TO} ", style="dim")
        k.append(str(b.k), style="bold yellow")
    console.print()
    console.print(_badge("compare", spec, k))

    a_head, b_head = _side_heads(comp)
    # On a recognised ablation pair the differing environment *is* the
    # experiment: cyan, never yellow.
    ablation = bool(comp.a_label or comp.b_label)
    grid = Table(
        box=None,
        show_header=True,
        header_style="bold cyan",
        padding=(0, 2),
        pad_edge=False,
    )
    grid.add_column("", style="dim", no_wrap=True)
    grid.add_column(f"A {_SEP} {a_head}" if a_head != "A" else "A")
    grid.add_column(f"B {_SEP} {b_head}" if b_head != "B" else "B")

    def run_id(meta: RunMeta) -> Text:
        text = Text(meta.timestamp.strftime("%Y-%m-%dT%H-%M-%SZ"), style="dim")
        if meta.interrupted:
            text.append("  interrupted", style="yellow")
        return text

    grid.add_row("run", run_id(a), run_id(b))
    # A different engine is the question a harness comparison asks, not a
    # mistake: caliper warns on none of it, so it is cyan.
    ea, eb = _engine_text(a.backend, a.model), _engine_text(b.backend, b.model)
    _env_row(grid, "engine", ea, eb, ea.plain != eb.plain, "bold cyan")
    if a.judge_backend or b.judge_backend:
        ja = _engine_text(a.judge_backend, a.judge_model or "default model")
        jb = _engine_text(b.judge_backend, b.judge_model or "default model")
        _env_row(
            grid,
            "judge",
            ja,
            jb,
            ja.plain != jb.plain,
            "yellow" if comp.judge_mismatch else "bold cyan",
        )
    sa, sb = comp.a_skills, comp.b_skills
    if sa or sb:
        style = "bold cyan" if ablation else "yellow"
        edited = Text()
        for record in comp.skill_drift:
            edited.append(
                f"  {record.name} edited",
                style="yellow" if record.source_kind == "git" else "cyan",
            )
        if set(sa) == set(sb):
            grid.add_row(
                "skills", _side_names(sa, sb, style), Text("same", style="dim") + edited
            )
        else:
            grid.add_row(
                Text("skills", style=style),
                _side_names(sa, sb, style),
                _side_names(sb, sa, style) + edited,
            )
    ma, mb = a.mcp_servers, b.mcp_servers
    if ma or mb or a.ablated_servers or b.ablated_servers:
        style = "bold cyan" if ablation else "yellow"
        ta = (
            _side_names(ma or [], mb or [], style)
            if ma is not None
            else Text("not recorded", style="dim")
        )
        tb = (
            _side_names(mb or [], ma or [], style)
            if mb is not None
            else Text("not recorded", style="dim")
        )
        _env_row(grid, "mcp", ta, tb, set(ma or []) != set(mb or []), style)
    ua, ub = _setup_text(a), _setup_text(b)
    _env_row(grid, "setup", ua, ub, ua.plain != ub.plain, "yellow")
    console.print(grid)


def _is_probe(outcomes: list[Outcome]) -> bool:
    """A trigger probe's side: unchecked attempts and no execution verdict."""
    return any(o == Outcome.NOT_CHECKED for o in outcomes) and not any(
        o in (Outcome.PASS, Outcome.TASK_FAIL) for o in outcomes
    )


def _pair_cell(before: float | None, after: float | None) -> Text:
    """`x → y` for a secondary metric (pass@k / pass^k)."""
    return Text(f"{_fmt_score(before)} {_TO} {_fmt_score(after)}", style="dim")


def _alt_metric(outcomes: list[Outcome], formula: Callable[[int, int], float | None]):
    """A secondary metric from the stored outcomes, through the same formulas
    ``TaskResult`` uses, so it can never disagree with the run it came from."""
    counts = OutcomeCounts(outcomes)
    return formula(counts.successes, counts.usable)


def print_comparison(comp: RunComparison, verbose: bool = False) -> None:
    """Render a two-run diff. A thin shell over ``diff_runs`` — no logic here."""
    _print_compare_header(comp)
    a_head, b_head = _side_heads(comp)
    console.print()

    # The two sides are the column headers, so no cell needs an arrow.
    table = Table(box=box.ROUNDED, header_style="bold cyan")
    table.add_column(_two_line("", "Task"))
    # Only the task name may wrap: a wrapped rate cell splits its marks.
    # A labelled pair names its sides; two plain runs read as before → after.
    labelled = bool(comp.a_label or comp.b_label)
    table.add_column(_two_line("A" if labelled else "before", a_head), no_wrap=True)
    table.add_column(_two_line("B" if labelled else "after", b_head), no_wrap=True)
    table.add_column(_two_line("", _DELTA), justify="right", no_wrap=True)

    for tc in comp.matched:
        measured = tc.a_score is not None and tc.b_score is not None
        name = Text(tc.task_name, style="" if measured else "dim")
        if _is_probe(tc.a_outcomes) and _is_probe(tc.b_outcomes):
            # A trigger probe has no execution score on either side by
            # construction; its activation diff is below.
            row = [
                Text(tc.task_name),
                _empty_rate("probe"),
                _empty_rate("probe"),
                Text(_RULE, style="dim"),
            ]
        else:
            after = "dim"
            if measured:
                after = (
                    "bold red"
                    if tc.regression
                    else "bold green"
                    if tc.b_score > tc.a_score
                    else ""
                )
            row = [
                name,
                _rate_cell(
                    tc.a_score,
                    _outcome_marks(tc.a_outcomes, comp.a.k),
                    "" if measured else "dim",
                ),
                _rate_cell(tc.b_score, _outcome_marks(tc.b_outcomes, comp.b.k), after),
                _pp(tc.delta, tc.regression),
            ]
        table.add_row(*row)

    table.add_section()
    comparable = [
        tc for tc in comp.matched if tc.a_score is not None and tc.b_score is not None
    ]
    delta = comp.aggregate_delta
    if comparable:

        def pooled(side: str) -> tuple[int, int]:
            counts = [
                OutcomeCounts(tc.a_outcomes if side == "a" else tc.b_outcomes)
                for tc in comparable
            ]
            return sum(c.successes for c in counts), sum(c.usable for c in counts)

        (sa, ua), (sb, ub) = pooled("a"), pooled("b")
        table.add_row(
            Text("Success", style="bold"),
            _rate_cell(
                comp.a_matched_avg,
                _avg_detail(comp.a_matched_avg, sa, ua, len(comparable)),
                "bold",
            ),
            _rate_cell(
                comp.b_matched_avg,
                _avg_detail(comp.b_matched_avg, sb, ub, len(comparable)),
                "bold green" if delta > 0 else "bold red" if delta < 0 else "bold",
            ),
            _pp(delta),
        )
    else:
        # With no task measured on both sides the averages are empty, not 0%,
        # and a "+0 pp" would read as a comparison that held steady.
        table.add_row(
            Text("Success", style="bold"),
            _empty_rate(),
            _empty_rate(),
            Text("no task measured on both sides", style="dim"),
        )
    _cost_rows(table, comp.a_usage, comp.b_usage)
    console.print(table)

    if verbose:
        _print_secondary_metrics(comp)
    _print_activation_diff(comp)
    _print_notes(_compare_notes(comp))
    console.print()


def _print_secondary_metrics(comp: RunComparison) -> None:
    """pass@k and pass^k under --verbose, in a table of their own: as two more
    columns they would squeeze the rate cells the main table is for."""
    a_head, b_head = _side_heads(comp)
    console.print()
    console.print(
        Text.assemble(
            (" Secondary metrics", "bold"),
            (f"  {a_head} {_TO} {b_head} · from the same outcomes", "dim"),
        )
    )
    table = Table(box=box.ROUNDED, header_style="bold cyan")
    table.add_column("Task")
    table.add_column("pass@k", no_wrap=True)
    table.add_column("pass^k", no_wrap=True)
    for tc in comp.matched:
        if _is_probe(tc.a_outcomes) and _is_probe(tc.b_outcomes):
            continue
        table.add_row(
            Text(tc.task_name),
            _pair_cell(
                _alt_metric(tc.a_outcomes, pass_at_k),
                _alt_metric(tc.b_outcomes, pass_at_k),
            ),
            _pair_cell(
                _alt_metric(tc.a_outcomes, pass_hat_k),
                _alt_metric(tc.b_outcomes, pass_hat_k),
            ),
        )
    console.print(table)


def _cost_rows(table: Table, a: UsageTotals, b: UsageTotals) -> None:
    """Tokens and wall per side, each beside its per-attempt cost. Secondary:
    green when the after side is cheaper, and never a regression."""

    def cell(total: str, each: str | None) -> Text:
        text = Text(total.rjust(_RATE_W))
        # No attempt count, no per-attempt figure: dividing by a guessed 1
        # would print the total twice.
        if each is not None:
            text.append(f"  {each} each", style="dim")
        return text

    def tokens_each(u: UsageTotals) -> str | None:
        # Over the usable attempts, like wall: unusable spend is its own line.
        if not u.usable_attempts:
            return None
        return _fmt_tokens((u.total_tokens - u.unusable_tokens) // u.usable_attempts)

    def wall_each(u: UsageTotals) -> str | None:
        # Over the usable attempts: unusable spend is reported on its own line.
        if not u.usable_attempts:
            return None
        return f"{u.usable_wall_seconds / u.usable_attempts:.1f}s"

    if a.tokens_reported and b.tokens_reported:
        table.add_row(
            Text("Tokens", style="bold"),
            cell(_fmt_tokens(a.total_tokens), tokens_each(a)),
            cell(_fmt_tokens(b.total_tokens), tokens_each(b)),
            _relative(a.total_tokens, b.total_tokens, lambda n: _fmt_tokens(int(n))),
        )
    table.add_row(
        Text("Wall", style="bold"),
        cell(_fmt_duration(a.wall_seconds), wall_each(a)),
        cell(_fmt_duration(b.wall_seconds), wall_each(b)),
        _relative(a.wall_seconds, b.wall_seconds, _fmt_duration),
    )


def _print_activation_diff(comp: RunComparison) -> None:
    """The second scoreboard, never merged into the first (docs/adr/0014).

    Only tasks measured on both sides: on a skill-ablation pair one side has no
    verdicts at all, and a column of dashes would say nothing. When nothing
    moved it is one line, not a table of equal numbers.
    """
    both = [
        tc
        for tc in comp.matched
        if tc.a_activation is not None and tc.b_activation is not None
    ]
    if not both:
        return
    a_head, b_head = _side_heads(comp)
    a_avg = sum(tc.a_activation for tc in both) / len(both)
    b_avg = sum(tc.b_activation for tc in both) / len(both)
    console.print()
    if all(not tc.activation_delta for tc in both):
        plural = "s" if len(both) > 1 else ""
        console.print(
            Text.assemble(
                (" Activation", "bold"),
                (
                    f"  {_fmt_score(a_avg)} on both sides {_SEP} unchanged on {len(both)} task{plural}",
                    "dim",
                ),
            )
        )
        return
    console.print(
        Text.assemble(
            (" Activation", "bold"),
            ("  per task · a separate scoreboard from success", "dim"),
        )
    )
    table = Table(box=box.ROUNDED, header_style="bold cyan")
    table.add_column("Task")
    table.add_column(a_head, justify="right")
    table.add_column(b_head, justify="right")
    table.add_column(_DELTA, justify="right")
    for tc in both:
        after = (
            "bold red"
            if tc.activation_regression
            else "bold green"
            if (tc.activation_delta or 0) > 0
            else ""
        )
        table.add_row(
            Text(tc.task_name),
            Text(_fmt_score(tc.a_activation)),
            Text(_fmt_score(tc.b_activation), style=after),
            _pp(tc.activation_delta, tc.activation_regression),
        )
    table.add_section()
    table.add_row(
        Text("Activation", style="bold"),
        Text(_fmt_score(a_avg), style="bold"),
        Text(
            _fmt_score(b_avg),
            style="bold green"
            if b_avg > a_avg
            else "bold red"
            if b_avg < a_avg
            else "bold",
        ),
        _pp(b_avg - a_avg),
    )
    console.print(table)


def _compare_notes(comp: RunComparison) -> list[_Note]:
    notes: list[_Note] = []
    regressions = [tc.task_name for tc in comp.matched if tc.regression]
    if regressions:
        n = len(regressions)
        notes.append(
            _Note(
                0,
                _DOWN,
                f"{n} regression{'s' if n > 1 else ''}",
                ", ".join(regressions),
            )
        )
    # Its own line, never merged with the execution regressions: a description
    # that stopped firing and a body that stopped working are fixed in different
    # places, so naming them together would hide which one moved.
    activation = [tc.task_name for tc in comp.matched if tc.activation_regression]
    if activation:
        n = len(activation)
        notes.append(
            _Note(
                0,
                _DOWN,
                f"{n} activation regression{'s' if n > 1 else ''}",
                ", ".join(activation),
                "the description stopped firing, not the body",
            )
        )
    notes += [_warning_note(w) for w in comp.warnings]
    # Path-source drift is *shown*, not warned about: it is the everyday "edit
    # skill, re-run" loop. The git-sourced records are already in `warnings`.
    # See docs/CONTEXT.md → Skill drift.
    for record in comp.skill_drift:
        if record.source_kind != "git":
            notes.append(
                _Note(
                    2,
                    _SEP,
                    f"{record.name} edited",
                    f"{record.a_ref} {_TO} {record.b_ref}",
                    "path source · the change under test",
                )
            )
    unmeasured = [
        tc.task_name
        for tc in comp.matched
        if (tc.a_score is None or tc.b_score is None)
        and not (_is_probe(tc.a_outcomes) and _is_probe(tc.b_outcomes))
    ]
    if unmeasured:
        notes.append(
            _Note(
                1,
                _UNUSABLE,
                f"{len(unmeasured)} unmeasured",
                ", ".join(unmeasured),
                "excluded from the success average",
            )
        )
    if comp.unmatched_a or comp.unmatched_b:
        # A and B, which the header defines, read better than "only in with x".
        a_head, b_head = "A", "B"
        body = Text()

        if comp.unmatched_a:
            body.append(f"only in {a_head}: ", style="dim")
            body.append(", ".join(comp.unmatched_a))
        if comp.unmatched_b:
            if len(body):
                body.append("   ")
            body.append(f"only in {b_head}: ", style="dim")
            body.append(", ".join(comp.unmatched_b))
        notes.append(_Note(2, _SEP, "unmatched", body))
    return notes


def comparison_to_json(comp: RunComparison) -> str:
    return comp.model_dump_json(indent=2)
