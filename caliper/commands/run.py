from __future__ import annotations

import signal
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

import typer
from pydantic import ValidationError
from rich.console import Console

from caliper import cancel
from caliper.commands.diagnosis import BadInput, CannotRun, ExitCode, fail
from caliper.harness.base import HarnessConfigurationError
from caliper.skillfetch import SkillFetcher
from caliper.skills import SkillResolutionError
from caliper.harness import get_harness
from caliper.judge import EvalJudge
from caliper.reporter import (
    SEP_GLYPH,
    UNUSABLE_GLYPH,
    WARN_GLYPH,
    make_progress,
    print_banner,
    print_results,
    update_progress,
)
from caliper.runstore import RunStore
from caliper.environment import choose_user_customizations
from caliper.runner import run, AttemptEvent, RunAborted
from caliper.schema.results import OutcomeCounts, RunResults, TaskResult
from caliper.schema.spec import (
    DEFAULT_BACKEND,
    VALID_BACKENDS,
    load_spec,
    parse_target,
    spec_name,
)

console = Console()


@contextmanager
def _interrupt_guard(console: Console) -> Iterator[None]:
    """Make the first Ctrl-C a graceful stop and the second a hard quit.

    The first interrupt asks the run to stop and kills the agents in flight, so
    the attempts already paid for can be saved. It deliberately does **not**
    raise: a ``KeyboardInterrupt`` here would unwind through
    ``ThreadPoolExecutor.__exit__``, which waits for every in-flight attempt —
    up to ``--timeout`` each — before the exception is even seen, and then
    discards the whole run. The default handler is restored on the way in, so a
    caller who wants out *now* just presses it again.
    """

    def handle(signum: int, frame: object) -> None:
        signal.signal(signal.SIGINT, previous)
        console.print(
            "\n[yellow]⊘ Stopping.[/yellow] Killing the attempts in flight and "
            "saving what already ran.\n[dim]  Ctrl-C again to quit without "
            "saving.[/dim]"
        )
        cancel.request()

    previous = signal.getsignal(signal.SIGINT)
    try:
        signal.signal(signal.SIGINT, handle)
    except ValueError:
        # Not the main thread (an embedder calling the command directly), where
        # a handler cannot be installed. The run still works; Ctrl-C just falls
        # back to the default behaviour.
        yield
        return
    try:
        yield
    finally:
        signal.signal(signal.SIGINT, previous)


def run_cmd(
    spec_file: Path = typer.Argument(..., help="Path to .eval.yaml spec file"),
    k: int = typer.Option(3, "--k", help="Attempts per task"),
    workers: int = typer.Option(
        4, "--workers", help="Attempts to run in parallel, across all tasks"
    ),
    timeout: int = typer.Option(120, "--timeout", help="Seconds per attempt"),
    fail_fast_unusable: int = typer.Option(
        0,
        "--fail-fast",
        min=0,
        help=(
            "Stop a task after N consecutive infra_error/timeout attempts "
            "(0 disables). Counts attempts, not invocations: a throttled "
            "attempt that retried and then ran is one healthy attempt."
        ),
    ),
    ablate: Optional[list[str]] = typer.Option(
        None,
        "--ablate",
        help=(
            "Run without this declared skill or mcp: server (repeatable). "
            "Qualify with skill:/mcp: if both declare the name. Diff it against "
            "a full run with `caliper compare`."
        ),
    ),
    baseline: bool = typer.Option(
        False, "--baseline", hidden=True, help="Retired — see --ablate"
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
    user_customizations: Optional[bool] = typer.Option(
        None,
        "--user-customizations/--no-user-customizations",
        show_default=False,
        help=(
            "Whether attempts load user skills, plugins, rules, settings and "
            "connectors alongside the spec's skills and mcp: "
            "(the spec wins a name clash). Omitted: the spec's "
            "user_customizations, else on. Use --no-user-customizations for a "
            "portable score or a harness comparison. Judge connector controls are unchanged."
        ),
    ),
) -> None:
    # Retired in favour of --ablate, which runs *one* arm and saves it as an
    # ordinary run. Kept parseable for one release because caliper ships on PyPI
    # and typer's bare "No such option" would tell an outside caller nothing
    # about where the capability went. Not silently remapped: --baseline ran two
    # arms in one invocation, so honouring the old name over the new semantics
    # would halve a scripted caller's spend and stop rendering the delta it was
    # reading. See
    # docs/adr/0015-ablation-names-its-subject-at-the-invocation.md.
    if baseline:
        fail(
            CannotRun(
                "`--baseline` has been retired.\n\n"
                "It ran a second, no-skill arm inside every invocation, re-paying "
                "for a number that cannot move when the skill changes: the "
                "no-skill arm has no skill in it.\n\n"
                "Run the arm once and keep it:\n"
                "  caliper run <spec> --ablate <skill-name>\n"
                "  caliper compare <that-run> <your-run>\n\n"
                "Name every declared skill to get the bare agent. The saved arm "
                "is reusable across every later iteration of the skill.",
                title="Retired flag",
            )
        )

    if k < 1:
        fail(
            BadInput(f"--k must be at least 1, got {k}: the run would measure nothing.")
        )
    if workers < 1:
        fail(BadInput(f"--workers must be at least 1, got {workers}."))
    if timeout < 1:
        fail(
            BadInput(
                f"--timeout must be at least 1 second, got {timeout}: every "
                "attempt would time out before the agent started."
            )
        )
    if not spec_file.exists():
        fail(BadInput(f"File not found: {spec_file}"))

    try:
        spec = load_spec(spec_file)
    except ValidationError as exc:
        # A schema failure gets the table's panel — the same verdict `validate`
        # reaches, rather than a second opinion in a different shape.
        fail(exc)
    except Exception as exc:
        # A YAML syntax error, or the retired `skill:` key. Not a schema
        # verdict, so it stays a one-line statement of what is wrong.
        fail(BadInput(f"Invalid spec: {exc}"))

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

    # Before the banner and before any attempt: a misspelt backend would
    # otherwise surface as a traceback (--model) or as a judge_error on every
    # attempt after the agent was already paid for (--judge-model).
    for flag, chosen in (("--model", backend), ("--judge-model", judge_backend)):
        if chosen not in VALID_BACKENDS:
            fail(
                CannotRun(
                    f"Unknown backend {chosen!r} in {flag}.\n\n"
                    f"Known backends: {', '.join(sorted(VALID_BACKENDS))}.\n"
                    f"Pass {flag} <backend>[:<model>], e.g. {flag} codex:gpt-5-codex, "
                    f"or a bare model name for the default {DEFAULT_BACKEND} backend.",
                    title="Unknown backend",
                )
            )

    name = spec_name(spec_file)
    print_banner(name, k, backend, skill_model)

    harness = get_harness(backend, skill_model)
    judge = EvalJudge(judge_backend, judge_model_name)

    # A notice, not a prompt: an attempt's isolation was never a security
    # boundary (docs/adr/0027, docs/adr/0028), and a run must stay usable
    # non-interactively. Loud when a flag or the spec asked for it, named by its
    # source; one dim line when the default applied, since that is every run. A
    # backend without MCP gets the run's no-effect warning instead
    # (caliper/environment.py).
    customizations = choose_user_customizations(user_customizations, spec, harness)
    if customizations.load:
        if customizations.explicit:
            source = (
                "--user-customizations"
                if customizations.source == "flag"
                else "user_customizations: true (spec)"
            )
            console.print(
                f"[yellow]⚠ {source}:[/yellow] attempts get this machine's user "
                "customizations (skills, plugins, rules, settings and connectors). The score "
                "depends on this setup, and attempts can act on those accounts "
                "without asking."
                + (
                    "\n[dim]  --no-user-customizations runs it isolated.[/dim]"
                    if user_customizations is None
                    else ""
                )
            )
        else:
            console.print(
                "[dim]Loading this machine's user customizations (skills, plugins, "
                "rules, settings and connectors); attempts can use them without asking.\n"
                "  --no-user-customizations to isolate.[/dim]"
            )

    task_names = [t.name for t in spec.tasks]
    progress, task_ids = make_progress(task_names, k)

    # `run` fetches, unlike `validate`: the fetch happens before the first
    # attempt, so an unreachable repo: costs nothing. A stale-cache warning is
    # pushed out as it happens rather than collected and printed afterwards —
    # collecting would lose it entirely on the runs that then fail, which are
    # exactly the runs where knowing a member was stale matters most.
    def warn(message: str) -> None:
        progress.console.print(f"[yellow]{WARN_GLYPH} {message}[/yellow]")

    fetcher = SkillFetcher(on_warning=warn)

    # Each task's outcome counts, keyed by id, that the live view renders. A
    # task that stops short of k gets its final row from its result instead.
    names = {t.id: t.name for t in spec.tasks}
    task_counts = {t.id: OutcomeCounts() for t in spec.tasks}

    def on_attempt_done(event: AttemptEvent) -> None:
        name = names.get(event.task_id)
        if name is None:
            return
        counts = task_counts[event.task_id]
        counts.add(event.outcome)
        # `is_execution_noise`, not `not is_usable`: a NOT_CHECKED trigger probe
        # is a healthy attempt, and flagging it live as yellow ⊘ told a watching
        # agent to stop for a run in which nothing had gone wrong.
        if event.outcome.is_execution_noise:
            # Surface noise the moment it lands so a watching agent/human can stop.
            progress.console.print(
                f"[yellow]{UNUSABLE_GLYPH}[/yellow] {name} {SEP_GLYPH} attempt {event.attempt}: "
                f"[yellow]{event.outcome.value}[/yellow]"
            )
        update_progress(progress, task_ids, name, k, counts=counts)

    def on_task_done(result: TaskResult) -> None:
        if len(result.attempts) >= k:
            return
        update_progress(
            progress,
            task_ids,
            result.task_name,
            k,
            counts=result.counts,
            finished=True,
        )

    aborted: RunAborted | None = None
    with progress, _interrupt_guard(progress.console):
        try:
            results = run(
                spec=spec,
                spec_path=spec_file,
                harness=harness,
                judge=judge,
                k=k,
                workers=workers,
                timeout=timeout,
                fail_fast_unusable=fail_fast_unusable,
                ablate=list(ablate or []),
                fetcher=fetcher,
                on_warning=warn,
                on_attempt_done=on_attempt_done,
                on_task_done=on_task_done,
                user_customizations=user_customizations,
            )
        except (SkillResolutionError, HarnessConfigurationError) as exc:
            fail(exc)
        except RunAborted as exc:
            # A fatal error mid-run. The attempts that already ran are on the
            # exception, and they get saved and rendered exactly like any other
            # run before the cause is shown.
            aborted, results = exc, exc.results

    _save_and_report(results, spec_file, output, verbose)

    if aborted is not None:
        # Which of the two causes it was, and what that exits with, is the
        # diagnosis table's call — not this module's.
        fail(aborted)
    if results.run.interrupted:
        raise typer.Exit(ExitCode.INTERRUPTED)
    nothing_measured = _nothing_measured(results)
    if nothing_measured is not None:
        fail(CannotRun(nothing_measured))
    if results.run.hook_failures:
        raise typer.Exit(ExitCode.CANNOT_RUN)


def _nothing_measured(results: RunResults) -> str | None:
    """Why the run measured nothing, or ``None`` if any attempt was measured.

    Only execution noise counts against a run, so an all-``not_checked`` trigger
    probe still exits ``0`` (docs/CONTEXT.md → Exit code).
    """
    attempts = sum(len(task.attempts) for task in results.task_results)
    counts = results.noise_counts
    if not attempts or sum(counts.values()) < attempts:
        return None
    breakdown = ", ".join(f"{n} {outcome.value}" for outcome, n in counts.items())
    return f"No attempt was usable ({breakdown}) — the run measured nothing."


def _save_and_report(
    results: RunResults, spec_file: Path, output: Optional[Path], verbose: bool
) -> None:
    """Persist the run and render it — the same path for a whole or partial run.

    An interrupted run is saved as an ordinary run file: every metric already
    divides by *usable* attempts rather than k (docs/adr/0007), so a smaller
    sample scores correctly, and ``RunMeta.interrupted`` is what says the sample
    is short. Nothing about the file needs a reader to know it was cut off.

    A run where **nothing** ran is usually omitted — a spending cap on the
    first invocation, a Ctrl-C during skill fetching. A hook failure is the
    exception: its diagnostic must be saved even if no attempt was recorded.
    """
    if (
        not any(task.attempts for task in results.task_results)
        and not results.run.hook_failures
    ):
        console.print("[dim]Nothing ran — no results saved.[/dim]")
        return

    # The same root every reading command resolves, discovered the same way
    # (docs/adr/0022-saved-runs-live-at-a-discovered-results-root.md).
    saved_path = RunStore.discover().save(results)
    if output:
        try:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(results.model_dump_json(indent=2))
        except OSError as exc:
            console.print(f"[yellow]Could not write --output {output}: {exc}[/yellow]")

    print_results(results, verbose=verbose)
    console.print(f"[dim]Results saved to {saved_path}[/dim]")
