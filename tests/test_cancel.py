"""Ctrl-C stops a run without throwing away what it already paid for.

The behaviours under test: attempts already finished are kept and saved,
attempts the cancellation killed are dropped rather than recorded as
infrastructure failures, an agent in flight is actually killed (not waited out),
and a fatal error diagnosed mid-run salvages the same way an interrupt does.
"""

from __future__ import annotations

import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest
from typer.testing import CliRunner

from caliper import cancel
from caliper.harness.base import (
    AttemptResult,
    CliHarness,
    ConversationTurn,
    HarnessBackend,
    HarnessConfigurationError,
)
from caliper.judge.base import JudgeResult
from caliper.main import app
from caliper.runner import RunAborted, run
from caliper.schema.results import (
    AggregateScore,
    AttemptRecord,
    Outcome,
    RunMeta,
    RunResults,
    TaskResult,
)
from caliper.schema.spec import EvalSpec, TaskSpec

runner = CliRunner()


class PassingJudge:
    def evaluate(self, task, transcript, final_output, spec_dir) -> JudgeResult:
        return JudgeResult(passed=True, reasoning="ok")


class CancellingHarness(HarnessBackend):
    """Finishes one attempt, then asks the run to stop — like a Ctrl-C mid-run."""

    def __init__(self, cancel_after: int = 1, then_fail: bool = False) -> None:
        self.cancel_after = cancel_after
        self.then_fail = then_fail
        self.started: list[int] = []

    @property
    def name(self) -> str:
        return "cancelling"

    def run(self, task_id: str, attempt: int, prompt: str, **kwargs) -> AttemptResult:
        self.started.append(attempt)
        if attempt >= self.cancel_after:
            cancel.request()
        if self.then_fail:
            # What a killed agent looks like coming back: a non-zero exit,
            # nothing to show for it, and — the part only the spawn knows —
            # `cancelled`, saying *we* killed it rather than it failing.
            return AttemptResult(
                task_id=task_id,
                attempt=attempt,
                transcript=[],
                final_output="",
                exit_code=-9,
                duration_seconds=0.01,
                error="killed",
                cancelled=True,
            )
        return AttemptResult(
            task_id=task_id,
            attempt=attempt,
            transcript=[ConversationTurn(role="assistant", content="done")],
            final_output="done",
            exit_code=0,
            duration_seconds=0.01,
        )


class ExpiringHarness(HarnessBackend):
    """Runs, then starts failing the way an expired credential does."""

    def __init__(self, fail_from: int = 2) -> None:
        self.fail_from = fail_from

    @property
    def name(self) -> str:
        return "expiring"

    def run(self, task_id: str, attempt: int, prompt: str, **kwargs) -> AttemptResult:
        if attempt >= self.fail_from:
            raise HarnessConfigurationError("credentials expired mid-run")
        return AttemptResult(
            task_id=task_id,
            attempt=attempt,
            transcript=[],
            final_output="done",
            exit_code=0,
            duration_seconds=0.01,
        )


class SleepHarness(CliHarness):
    """A CLI harness whose agent is ``sleep`` — something to interrupt."""

    @property
    def name(self) -> str:
        return "sleep"

    def skills_root(self, ctx) -> Path:  # pragma: no cover - never installs
        return Path(ctx.isolated_home) / "skills"

    def _command(self, ctx):  # pragma: no cover - driven through _execute
        return ["sleep", "30"], None, None

    def _environment(self, ctx) -> dict[str, str]:  # pragma: no cover
        return {}

    def _parse_stream(self, stdout: str):
        return [], stdout


def _spec(n_tasks: int = 1) -> EvalSpec:
    return EvalSpec(
        tasks=[
            TaskSpec(
                id=f"task-{i + 1:03d}",
                name=f"Task {i + 1}",
                prompt="Do the thing",
                assert_script="assert True",
            )
            for i in range(n_tasks)
        ]
    )


def _spec_file(tmp_path: Path) -> Path:
    path = tmp_path / "sample.eval.yaml"
    path.write_text("tasks: []\n")
    return path


def test_cancelling_keeps_the_attempts_already_paid_for(tmp_path) -> None:
    harness = CancellingHarness(cancel_after=1)

    results = run(
        spec=_spec(),
        spec_path=_spec_file(tmp_path),
        harness=harness,
        judge=PassingJudge(),
        k=5,
        workers=1,
        timeout=5,
    )

    attempts = results.task_results[0].attempts
    assert [a.attempt for a in attempts] == [1]
    assert attempts[0].outcome == Outcome.PASS
    assert results.run.interrupted is True
    # Scored over what ran, not over the k that was asked for.
    assert results.task_results[0].score == 1.0


def test_a_cancelled_run_does_not_start_the_remaining_attempts(tmp_path) -> None:
    harness = CancellingHarness(cancel_after=1)

    run(
        spec=_spec(n_tasks=2),
        spec_path=_spec_file(tmp_path),
        harness=harness,
        judge=PassingJudge(),
        k=4,
        workers=1,
        timeout=5,
    )

    # One attempt ran and cancelled the run; nothing after it was started.
    assert harness.started == [1]


class FailsOnItsOwnHarness(HarnessBackend):
    """Fails for real, and cancels the run from underneath itself.

    The awkward case: an attempt that was dying of its own causes while the
    interrupt landed. It was never killed, so its failure is a real observation.
    """

    @property
    def name(self) -> str:
        return "failing"

    def run(self, task_id: str, attempt: int, prompt: str, **kwargs) -> AttemptResult:
        cancel.request()
        return AttemptResult(
            task_id=task_id,
            attempt=attempt,
            transcript=[],
            final_output="",
            exit_code=1,
            duration_seconds=0.01,
            error="Error 503: service unavailable",
            # Not cancelled: nothing killed this, it fell over on its own.
        )


def test_attempts_killed_by_the_cancellation_are_not_recorded(tmp_path) -> None:
    """An interrupt must not show up in the sample as an infrastructure failure."""
    # Attempt 1 is killed by the very cancellation it triggers — the shape of a
    # SIGKILL landing on an agent that was mid-flight.
    harness = CancellingHarness(cancel_after=1, then_fail=True)

    results = run(
        spec=_spec(),
        spec_path=_spec_file(tmp_path),
        harness=harness,
        judge=PassingJudge(),
        k=3,
        workers=1,
        timeout=5,
    )

    assert harness.started == [1]
    assert results.task_results[0].attempts == []
    assert results.run.interrupted is True


def test_an_attempt_that_failed_on_its_own_is_kept(tmp_path) -> None:
    """Dropping on the outcome would delete the evidence you interrupted.

    Interrupt a run during a real throttling storm and the dead attempts are of
    two kinds: the ones the storm killed (observations) and the ones the
    interrupt killed (artefacts). Only the spawn knows which is which.
    """
    results = run(
        spec=_spec(),
        spec_path=_spec_file(tmp_path),
        harness=FailsOnItsOwnHarness(),
        judge=PassingJudge(),
        k=3,
        workers=1,
        timeout=5,
    )

    attempts = results.task_results[0].attempts
    assert [a.outcome for a in attempts] == [Outcome.INFRA_ERROR]
    assert results.task_results[0].unusable == 1
    assert results.run.interrupted is True


def test_a_fatal_error_mid_run_salvages_the_attempts_that_ran(tmp_path) -> None:
    with pytest.raises(RunAborted) as excinfo:
        run(
            spec=_spec(),
            spec_path=_spec_file(tmp_path),
            harness=ExpiringHarness(fail_from=2),
            judge=PassingJudge(),
            k=4,
            workers=1,
            timeout=5,
        )

    aborted = excinfo.value
    assert isinstance(aborted.cause, HarnessConfigurationError)
    assert "credentials expired" in str(aborted.cause)
    assert [a.attempt for a in aborted.results.task_results[0].attempts] == [1]
    assert aborted.results.run.interrupted is True


def test_cancel_kills_an_agent_in_flight(tmp_path) -> None:
    """The point of the whole mechanism: not waiting out the timeout."""
    cancel.reset()
    harness = SleepHarness()
    finished = threading.Event()
    box: dict[str, object] = {}

    def spawn() -> None:
        box["result"] = harness._execute(
            ["sleep", "30"],
            env=dict(os.environ),
            cwd=str(tmp_path),
            timeout=30,
            stdin=None,
        )
        finished.set()

    thread = threading.Thread(target=spawn)
    started = time.monotonic()
    thread.start()
    # A cancellation that lands before the spawn is covered too — `track` kills
    # the process at registration when the flag is already set.
    time.sleep(0.2)
    cancel.request()

    assert finished.wait(timeout=10), "the agent was not killed"
    thread.join()
    assert time.monotonic() - started < 10
    assert box["result"].returncode != 0


def _one_attempt_run(k: int = 3) -> RunResults:
    """A partial run with something in it — the shape salvage exists to keep."""
    return RunResults(
        run=RunMeta(
            spec="sample",
            timestamp=datetime(2026, 7, 3, tzinfo=timezone.utc),
            k=k,
            backend="claude-code",
            interrupted=True,
        ),
        skill_snapshots=[],
        task_results=[
            TaskResult(
                task_id="task-001",
                task_name="One",
                attempts=[
                    AttemptRecord(
                        attempt=1,
                        output="ok",
                        duration_seconds=1.0,
                        outcome=Outcome.PASS,
                    )
                ],
            )
        ],
        aggregate=AggregateScore(avg_score=1.0, per_task=[]),
    )


def test_run_cli_exits_130_and_saves_an_interrupted_run(monkeypatch, tmp_path) -> None:
    # `run` roots its store at the cwd (docs/adr/0022), so the run this asserts
    # on lands under tmp_path only if that is where caliper is invoked from.
    monkeypatch.chdir(tmp_path)
    spec_file = tmp_path / "sample.eval.yaml"
    spec_file.write_text(
        "tasks:\n  - name: One\n    prompt: Do it\n    assert: 'assert True'\n"
    )

    def fake_run(**kwargs):
        return _one_attempt_run(k=kwargs["k"])

    monkeypatch.setattr("caliper.commands.run.get_harness", lambda *a, **kw: object())
    monkeypatch.setattr("caliper.commands.run.EvalJudge", lambda *a, **kw: object())
    monkeypatch.setattr("caliper.commands.run.run", fake_run)

    result = runner.invoke(app, ["run", str(spec_file)])

    assert result.exit_code == 130
    assert "interrupted" in result.stdout
    saved = list((tmp_path / ".caliper" / "results" / "sample").glob("*.json"))
    assert len(saved) == 1


def test_run_cli_saves_before_reporting_a_fatal_error(monkeypatch, tmp_path) -> None:
    # `run` roots its store at the cwd (docs/adr/0022), so the run this asserts
    # on lands under tmp_path only if that is where caliper is invoked from.
    monkeypatch.chdir(tmp_path)
    spec_file = tmp_path / "sample.eval.yaml"
    spec_file.write_text(
        "tasks:\n  - name: One\n    prompt: Do it\n    assert: 'assert True'\n"
    )

    partial = _one_attempt_run()

    def fake_run(**kwargs):
        raise RunAborted(HarnessConfigurationError("credentials expired"), partial)

    monkeypatch.setattr("caliper.commands.run.get_harness", lambda *a, **kw: object())
    monkeypatch.setattr("caliper.commands.run.EvalJudge", lambda *a, **kw: object())
    monkeypatch.setattr("caliper.commands.run.run", fake_run)

    result = runner.invoke(app, ["run", str(spec_file)])

    assert result.exit_code == 2
    assert "credentials expired" in result.stdout
    saved = list((tmp_path / ".caliper" / "results" / "sample").glob("*.json"))
    assert len(saved) == 1
