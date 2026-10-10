from __future__ import annotations

import os
import shlex
import signal
import subprocess
import sys
import tempfile
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import NoReturn

import typer
from pydantic import ValidationError
from rich.console import Console
from rich.markup import escape

from caliper import cancel
from caliper.commands.diagnosis import BadInput, CannotRun, ExitCode, fail, render
from caliper.commands.engine import check_judge_cli, resolve_engine
from caliper.environment import choose_user_customizations
from caliper.harness.base import HarnessConfigurationError, LoginRequired
from caliper.reporter import (
    SEP_GLYPH,
    UNUSABLE_GLYPH,
    WARN_GLYPH,
    make_progress,
    print_banner,
    print_results,
    update_progress,
)
from caliper.runner import AttemptEvent, RunAborted, run
from caliper.runstore import RunStore, UnusableResultsRoot
from caliper.schema.results import Outcome, RunResults, TaskResult
from caliper.schema.spec import load_spec, spec_name
from caliper.skillfetch import SkillFetcher
from caliper.skills import SkillResolutionError

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
    ablate: list[str] | None = typer.Option(
        None,
        "--ablate",
        help=(
            "Run without this declared skill or mcp: server (repeatable). "
            "Qualify with skill:/mcp: if both declare the name. Diff it against "
            "a full run with `caliper compare`."
        ),
    ),
    output: Path | None = typer.Option(
        None, "--output", help="Save results JSON to path"
    ),
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="Show per-attempt reasoning"
    ),
    model: str | None = typer.Option(
        None,
        "--model",
        "-m",
        help="Override skill backend/model (e.g. codex:gpt-5-codex or claude-sonnet-4-6)",
    ),
    judge_model: str | None = typer.Option(
        None,
        "--judge-model",
        help=(
            "Judge backend/model (e.g. claude-code:claude-haiku-4-5-20251001). "
            "Default: the --model backend, on its CLI's default model"
        ),
    ),
    user_customizations: bool | None = typer.Option(
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
        # A YAML syntax error, or a retired key (ADR 0004). Not a schema
        # verdict, so it stays a one-line statement of what is wrong.
        fail(BadInput(f"Invalid spec: {exc}"))

    # Before the banner and before any attempt, so a misspelt backend is a
    # diagnosis rather than a traceback or a judge_error per paid attempt.
    try:
        engine = resolve_engine(model, judge_model)
    except CannotRun as exc:
        fail(exc)
    try:
        RunStore.discover().check()
    except UnusableResultsRoot as exc:
        fail(exc)

    def refuse_without_judge_cli() -> None:
        # Called by the runner once the spec's skills and servers resolved: a
        # bad skill source keeps its own diagnosis (exit 1) rather than being
        # masked by a missing judge CLI, and no attempt has been paid for yet.
        try:
            check_judge_cli(engine, spec)
        except CannotRun as exc:
            fail(exc)

    name = spec_name(spec_file)
    print_banner(name, k, engine.backend, engine.model)

    harness = engine.harness()
    judge = engine.judge()

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

    # Each task's outcomes keyed by attempt number, the one source the live view
    # renders: attempts finish out of order, and each mark sits in the slot the
    # report will number it by. A task that stops short of k gets its final row
    # from its result instead.
    names = {t.id: t.name for t in spec.tasks}
    task_attempts: dict[str, dict[int, Outcome]] = {t.id: {} for t in spec.tasks}
    # Attempts complete on worker threads. Recording an outcome and rendering
    # the row happen under one lock, so a row is never rendered from a snapshot
    # older than one already shown.
    live = threading.Lock()

    def on_attempt_done(event: AttemptEvent) -> None:
        name = names.get(event.task_id)
        if name is None:
            return
        # `is_execution_noise`, not `not is_usable`: a NOT_CHECKED trigger probe
        # is a healthy attempt, and flagging it live as yellow ⊘ told a watching
        # agent to stop for a run in which nothing had gone wrong.
        if event.outcome.is_execution_noise:
            # Surface noise the moment it lands so a watching agent/human can stop.
            # The glyph sits in the spinner's column, so the task name lines up
            # with the name in the progress row below it.
            progress.console.print(
                f"[yellow]{UNUSABLE_GLYPH}[/yellow] {escape(name)} "
                f"[dim]{SEP_GLYPH} attempt {event.attempt} {SEP_GLYPH}[/dim] "
                f"[yellow]{event.outcome.value}[/yellow]"
            )
        with live:
            task_attempts[event.task_id][event.attempt] = event.outcome
            update_progress(
                progress,
                task_ids,
                name,
                k,
                by_attempt=task_attempts[event.task_id],
            )

    def on_task_done(result: TaskResult) -> None:
        if len(result.attempts) >= k:
            return
        with live:
            update_progress(
                progress,
                task_ids,
                result.task_name,
                k,
                finished=True,
                by_attempt={a.attempt: a.outcome for a in result.attempts},
            )

    stopped: Exception | None = None
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
                before_attempts=refuse_without_judge_cli,
            )
        except (SkillResolutionError, HarnessConfigurationError) as exc:
            # Shown once the live view has closed, since a login stop may ask.
            stopped, results = exc, None
        except RunAborted as exc:
            # A fatal error mid-run. The attempts that already ran are on the
            # exception, and they get saved and rendered exactly like any other
            # run before the cause is shown.
            stopped, results = exc, exc.results

    saved = True
    if results is not None:
        saved = _save_and_report(results, spec_file, output, verbose)

    if stopped is not None:
        _stop(stopped)
    if saved is False:
        raise typer.Exit(ExitCode.CANNOT_RUN)
    if results.run.interrupted:
        raise typer.Exit(ExitCode.INTERRUPTED)
    nothing_measured = _nothing_measured(results)
    if nothing_measured is not None:
        fail(CannotRun(nothing_measured))
    if results.run.hook_failures:
        raise typer.Exit(ExitCode.CANNOT_RUN)


def _stop(exc: Exception) -> NoReturn:
    """Show why the run stopped, and exit with the code the diagnosis gives it.

    A lapsed login in an interactive terminal is first offered its login
    command, and the same command line reruns once that succeeds. A CI job or
    an agent gets the command in the error, with nothing waiting for input.
    """
    code = render(exc)
    login = exc.cause if isinstance(exc, RunAborted) else exc
    if (
        isinstance(login, LoginRequired)
        and login.command is not None
        and _interactive()
        and _confirm(
            f"Log in to {login.backend} now with `{shlex.join(login.command)}`?"
        )
        and _logged_in(login.command)
    ):
        console.print("Logged in. Rerunning the eval.")
        # Through the interpreter, so `python -m caliper.main` reruns as well as
        # the console script does.
        os.execv(sys.executable, [sys.executable, *sys.orig_argv[1:]])
    raise typer.Exit(code)


def _logged_in(command: list[str]) -> bool:
    try:
        returncode = subprocess.run(command).returncode
    except OSError as exc:
        console.print(f"Could not run `{shlex.join(command)}`: {exc}", soft_wrap=True)
        return False
    if returncode != 0:
        console.print(
            f"`{shlex.join(command)}` did not complete the login.", soft_wrap=True
        )
    return returncode == 0


def _interactive() -> bool:
    if "CI" in os.environ or not (sys.stdin.isatty() and sys.stdout.isatty()):
        return False
    try:
        # A job sent to the background with `&` still has the terminal, but
        # reading from it would suspend the run until someone types `fg`.
        return os.tcgetpgrp(sys.stdin.fileno()) == os.getpgrp()
    except (AttributeError, OSError):
        # No job control (Windows): a terminal on both ends is all there is.
        return True


def _confirm(question: str) -> bool:
    try:
        return typer.confirm(question, default=False)
    except typer.Abort:
        # Ctrl-D or Ctrl-C at the prompt declines. Left to typer, it would exit
        # 1, which the exit-code contract reserves for bad input.
        return False


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
    results: RunResults, spec_file: Path, output: Path | None, verbose: bool
) -> bool:
    """Persist the run and render it — the same path for a whole or partial run.

    An interrupted run is saved as an ordinary run file: every metric already
    divides by *usable* attempts rather than k (docs/adr/0007), so a smaller
    sample scores correctly, and ``RunMeta.interrupted`` is what says the sample
    is short. Nothing about the file needs a reader to know it was cut off.

    A run where **nothing** ran is usually omitted — a spending cap on the
    first invocation, a Ctrl-C during skill fetching. A hook failure is the
    exception: its diagnostic must be saved even if no attempt was recorded.

    False when the results root refused the run. The attempts are paid for, so
    the run is then written to a temporary file rather than lost.
    """
    if (
        not any(task.attempts for task in results.task_results)
        and not results.run.hook_failures
    ):
        console.print("[dim]Nothing ran — no results saved.[/dim]")
        return True

    # The same root every reading command resolves, discovered the same way
    # (docs/adr/0022-saved-runs-live-at-a-discovered-results-root.md).
    try:
        saved_path = RunStore.discover().save(results)
        not_saved = None
    except (OSError, UnusableResultsRoot) as exc:
        not_saved = exc
        with tempfile.NamedTemporaryFile(
            "w",
            prefix=f"caliper-{results.run.spec}-",
            suffix=".json",
            delete=False,
        ) as kept:
            kept.write(results.model_dump_json(indent=2))
        saved_path = Path(kept.name)
    if output:
        try:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(results.model_dump_json(indent=2))
        except OSError as exc:
            console.print(f"[yellow]Could not write --output {output}: {exc}[/yellow]")

    print_results(results, verbose=verbose)
    if not_saved is not None:
        render(
            CannotRun(
                f"Could not save the run under the results root: {not_saved}\n\n"
                f"It was written to {saved_path} instead.",
                title="Run not saved",
            )
        )
        return False
    console.print(f"[dim]Results saved to {saved_path}[/dim]")
    return True
