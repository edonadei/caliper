"""Every way a caliper command can fail, and what it exits with.

The README publishes an exit-code contract, and CI reads it: ``2`` means *the
eval could not run* (a broken pipeline) where ``3`` will mean *the skill did not
clear the bar* (the answer you asked for). The contract used to live nowhere —
each command re-derived its own mapping from failure to panel to code, and
``run`` re-inspected a ``RunAborted``'s cause with ``isinstance`` to pick a
title. Eighteen ``typer.Exit`` sites across five command modules, one rule
between them.

The table below is now that rule. A command raises or catches, then calls
:func:`fail`, which is the only place a *failure* names a code. One exit stays
at its call site — ``run``'s ``ExitCode.INTERRUPTED`` — because a saved partial
run is not a failure and has nothing to render; it takes its number from the
same enum. Two exceptions carry the two ordinary
verdicts on a request — :class:`BadInput` (the caller's spec, path or reference
is wrong) and :class:`CannotRun` (caliper cannot get far enough to answer) — and
the domain's own exceptions are mapped as they arrive.

Deliberately **not** in here: ``update-cli``. Its exits report a declined
confirmation, a missing npm, and npm's own return code — user-interaction
outcomes that have nothing to do with whether an eval ran. Folding them in would
make one table answer two unrelated questions.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import NoReturn

import typer
from pydantic import ValidationError
from rich.console import Console
from rich.panel import Panel

from caliper.compare import IncomparableRunsError
from caliper.harness.base import HarnessConfigurationError
from caliper.retry import SpendingCapReached
from caliper.runner import RunAborted
from caliper.runstore import UnreadableRun
from caliper.skills import SkillResolutionError

console = Console()


class ExitCode(IntEnum):
    """The published contract (README → Exit codes)."""

    OK = 0
    # Bad input: the spec, the path or the run reference is wrong.
    BAD_INPUT = 1
    # Could not run: a misconfigured backend, an exhausted account, a retired
    # flag. To CI this is a broken pipeline, not a failing skill.
    CANNOT_RUN = 2
    # Reserved for a *pre-registered* bar that a clean run did not clear —
    # the one verdict worth failing a pipeline on. Nothing raises it yet;
    # named here so the reservation is visible rather than only documented.
    BAR_NOT_MET = 3
    # Ctrl-C. The shell's own convention, so a script does not read a partial
    # run as a complete one.
    INTERRUPTED = 130


class CommandError(Exception):
    """A failure a command states itself, rather than one it caught.

    The subclass *is* the exit code, so no call site ever names one. A ``title``
    is carried only when the body earns a panel — a multi-line explanation with
    something to act on; a one-line statement of fact prints as one line.
    """

    code: ExitCode

    def __init__(self, body: str, *, title: str | None = None) -> None:
        super().__init__(body)
        self.body = body
        self.title = title


class BadInput(CommandError):
    """The request was wrong: a missing file, an invalid spec, a bad reference."""

    code = ExitCode.BAD_INPUT


class CannotRun(CommandError):
    """Caliper could not get far enough to answer the question asked."""

    code = ExitCode.CANNOT_RUN


@dataclass(frozen=True)
class Diagnosis:
    """What to show, and what to exit with."""

    body: str
    code: ExitCode
    # ``None`` renders one red line; a title renders a bordered panel.
    title: str | None = None


def diagnose(exc: Exception) -> Diagnosis:
    """The rendering and exit code for one failure — the whole table."""
    if isinstance(exc, RunAborted):
        # Two causes reach here, and they read very differently to whoever has
        # to act: one is an account to top up, the other a machine to fix. The
        # cause carries both its own message and its own code; this only says
        # that a run was already in flight when it happened.
        cause = diagnose(exc.cause)
        return Diagnosis(
            body=cause.body,
            code=cause.code,
            title=f"Run stopped: {cause.title or 'failed'}",
        )
    if isinstance(exc, (BadInput, CannotRun)):
        code = ExitCode.BAD_INPUT if isinstance(exc, BadInput) else ExitCode.CANNOT_RUN
        return Diagnosis(body=exc.body, code=code, title=exc.title)
    if isinstance(exc, SkillResolutionError):
        return Diagnosis(str(exc), ExitCode.BAD_INPUT, title="Invalid skills")
    if isinstance(exc, ValidationError):
        return Diagnosis(str(exc), ExitCode.BAD_INPUT, title="Validation failed")
    if isinstance(exc, IncomparableRunsError):
        # A hard stop, unlike the k/spec/neighbourhood warnings: a cross-era diff
        # looks entirely normal and would be believed (docs/adr/0013).
        return Diagnosis(str(exc), ExitCode.BAD_INPUT, title="Refusing to compare")
    if isinstance(exc, UnreadableRun):
        return Diagnosis(f"Error parsing results: {exc}", ExitCode.BAD_INPUT)
    if isinstance(exc, SpendingCapReached):
        return Diagnosis(str(exc), ExitCode.CANNOT_RUN, title="Spending cap reached")
    if isinstance(exc, HarnessConfigurationError):
        return Diagnosis(
            str(exc), ExitCode.CANNOT_RUN, title="Backend configuration error"
        )
    # Unmapped: caliper's own fault, not the caller's input. `2` rather than `1`
    # so a CI job reads it as a broken pipeline and stops, which is what an
    # unhandled failure is.
    return Diagnosis(str(exc), ExitCode.CANNOT_RUN, title="Unexpected error")


def fail(exc: Exception) -> NoReturn:
    """Render ``exc`` and exit with its code.

    ``NoReturn`` so a caller can treat it as the end of a branch — the name that
    was being assigned when it fired stays correctly unbound afterwards.

    **Never call this inside a broad ``except``.** It leaves through
    ``typer.Exit``, which subclasses ``RuntimeError``, so an enclosing
    ``except Exception`` (or ``except RuntimeError``) swallows the exit and
    turns a diagnosed failure into some other code path. Where a helper that
    can ``fail`` is used inside a ``try``, hoist the call out of it.
    """
    diagnosis = diagnose(exc)
    if diagnosis.title:
        console.print(
            Panel(
                diagnosis.body,
                title=f"[bold red]{diagnosis.title}[/bold red]",
                border_style="red",
            )
        )
    else:
        console.print(f"[bold red]Error:[/bold red] {diagnosis.body}")
    raise typer.Exit(diagnosis.code)
