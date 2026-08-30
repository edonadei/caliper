from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
import typer
from pydantic import BaseModel, ValidationError

from caliper.commands.diagnosis import (
    BadInput,
    CannotRun,
    ExitCode,
    diagnose,
    fail,
)
from caliper.compare import IncomparableRunsError
from caliper.harness.base import HarnessConfigurationError
from caliper.retry import SpendingCapReached
from caliper.runner import RunAborted
from caliper.runstore import UnreadableRun
from caliper.schema.results import AggregateScore, RunMeta, RunResults
from caliper.skills import SkillResolutionError


def _validation_error() -> ValidationError:
    class Model(BaseModel):
        n: int

    with pytest.raises(ValidationError) as caught:
        Model(n="not a number")
    return caught.value


def _results() -> RunResults:
    return RunResults(
        run=RunMeta(
            spec="demo",
            timestamp=datetime.now(tz=timezone.utc),
            k=1,
            backend="claude-code",
        ),
        skill_snapshots=[],
        task_results=[],
        aggregate=AggregateScore(avg_score=0.0, per_task=[]),
    )


def test_bad_input_exits_one() -> None:
    assert diagnose(BadInput("File not found: x")).code is ExitCode.BAD_INPUT


def test_cannot_run_exits_two() -> None:
    assert diagnose(CannotRun("The flag is retired")).code is ExitCode.CANNOT_RUN


def test_unresolvable_skills_are_bad_input() -> None:
    """A neighbourhood that will not resolve is the author's spec, not the box."""
    diagnosis = diagnose(SkillResolutionError("no such skill"))

    assert diagnosis.code is ExitCode.BAD_INPUT
    assert diagnosis.body == "no such skill"


def test_an_invalid_spec_is_bad_input() -> None:
    assert diagnose(_validation_error()).code is ExitCode.BAD_INPUT


def test_an_unreadable_run_is_bad_input() -> None:
    exc = UnreadableRun(Path("demo/2026-01-01.json"), ValueError("not json"))
    assert diagnose(exc).code is ExitCode.BAD_INPUT


def test_a_misconfigured_backend_cannot_run() -> None:
    """The eval could not run — a broken pipeline, not a failing skill."""
    diagnosis = diagnose(HarnessConfigurationError("claude CLI not found"))

    assert diagnosis.code is ExitCode.CANNOT_RUN
    assert diagnosis.title == "Backend configuration error"


def test_a_spending_cap_cannot_run() -> None:
    diagnosis = diagnose(SpendingCapReached("out of credit"))

    assert diagnosis.code is ExitCode.CANNOT_RUN
    assert diagnosis.title == "Spending cap reached"


def test_an_aborted_run_reports_its_cause() -> None:
    """The run's own title says it stopped; the cause says what to go fix."""
    aborted = RunAborted(SpendingCapReached("out of credit"), _results())
    diagnosis = diagnose(aborted)

    assert diagnosis.code is ExitCode.CANNOT_RUN
    assert diagnosis.title == "Run stopped: Spending cap reached"
    assert diagnosis.body == "out of credit"


def test_an_aborted_run_distinguishes_its_two_causes() -> None:
    """One is an account to top up, the other a machine to fix."""
    aborted = RunAborted(HarnessConfigurationError("expired"), _results())

    assert diagnose(aborted).title == "Run stopped: Backend configuration error"


def test_incomparable_runs_are_a_hard_stop_on_bad_input() -> None:
    """A cross-era diff looks entirely normal and would be believed (adr/0013)."""
    diagnosis = diagnose(IncomparableRunsError("different eras"))

    assert diagnosis.code is ExitCode.BAD_INPUT
    assert diagnosis.title == "Refusing to compare"


def test_an_unknown_failure_is_not_silently_bad_input() -> None:
    """An unmapped exception is caliper's own fault, not the caller's input."""
    diagnosis = diagnose(RuntimeError("boom"))

    assert diagnosis.code is ExitCode.CANNOT_RUN


def test_a_title_makes_it_a_panel() -> None:
    assert diagnose(BadInput("body", title="Validation failed")).title == (
        "Validation failed"
    )


def test_no_title_renders_as_one_line() -> None:
    assert diagnose(BadInput("File not found: x")).title is None


def test_fail_raises_typer_exit_with_the_code() -> None:
    with pytest.raises(typer.Exit) as caught:
        fail(CannotRun("nope"))

    assert caught.value.exit_code == ExitCode.CANNOT_RUN


def test_the_reserved_bar_code_is_named() -> None:
    """Documented in the README's exit-code table; nothing raises it yet."""
    assert ExitCode.BAR_NOT_MET == 3


def test_the_interrupt_code_is_named() -> None:
    assert ExitCode.INTERRUPTED == 130
