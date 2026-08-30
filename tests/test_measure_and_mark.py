"""Judge latency is measured, and a short sample is marked wherever it is read.

Two unrelated gaps with one shape: a number that looks trustworthy because
nothing on screen says otherwise. Judge time was recorded nowhere at all, and an
interrupted run's score rendered identically to a complete one in `list` and in
`compare`.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path

from typer.testing import CliRunner

from caliper.attempt import assemble_attempt
from caliper.activation import ActivationDetector
from caliper.compare import diff_runs
from caliper.harness.base import AttemptResult, ConversationTurn
from caliper.judge.base import JudgeResult
from caliper.main import app
from caliper.schema.results import (
    AggregateScore,
    AttemptRecord,
    Outcome,
    RunMeta,
    RunResults,
    TaskResult,
    UsageTotals,
)
from caliper.schema.spec import TaskSpec

runner = CliRunner()


class OpenSandbox:
    def violations(self, transcript: list[ConversationTurn]) -> list[str]:
        return []


class SlowJudge:
    def evaluate(self, task, transcript, final_output, spec_dir) -> JudgeResult:
        time.sleep(0.05)
        return JudgeResult(passed=True, reasoning="ok")


def _harness_result(*, exit_code: int = 0, timed_out: bool = False) -> AttemptResult:
    return AttemptResult(
        task_id="task-001",
        attempt=1,
        transcript=[ConversationTurn(role="assistant", content="done")],
        final_output="done",
        exit_code=exit_code,
        duration_seconds=1.0,
        timed_out=timed_out,
        error="timeout" if timed_out else None,
    )


def _assemble(task: TaskSpec, result: AttemptResult) -> AttemptRecord:
    return assemble_attempt(
        result,
        attempt=1,
        task=task,
        spec_dir=".",
        expected_activation=None,
        activation=ActivationDetector([], frozenset()),
        sandbox=OpenSandbox(),
        judge=SlowJudge(),
    ).record


def test_judge_time_is_recorded_on_a_graded_attempt() -> None:
    task = TaskSpec(id="task-001", name="One", prompt="Do it", expect="it works")

    record = _assemble(task, _harness_result())

    assert record.judge_seconds is not None
    assert record.judge_seconds >= 0.05


def test_judge_time_is_none_when_no_judge_ran() -> None:
    """The difference between a fast judge and no judge at all."""
    # An activates:-only task authors no execution check, so the judge is
    # deliberately skipped — a 0.0 here would read as a free judge.
    task = TaskSpec(id="task-001", name="One", prompt="Do it", activates=["s"])

    record = _assemble(task, _harness_result())

    assert record.outcome == Outcome.NOT_CHECKED
    assert record.judge_seconds is None


def test_judge_time_is_none_when_the_attempt_never_reached_the_judge() -> None:
    task = TaskSpec(id="task-001", name="One", prompt="Do it", expect="it works")

    record = _assemble(task, _harness_result(timed_out=True))

    assert record.outcome == Outcome.TIMEOUT
    assert record.judge_seconds is None


def _task_result(judge_seconds: list[float | None]) -> TaskResult:
    return TaskResult(
        task_id="task-001",
        task_name="One",
        attempts=[
            AttemptRecord(
                attempt=i + 1,
                output="ok",
                duration_seconds=1.0,
                outcome=Outcome.PASS,
                judge_seconds=js,
            )
            for i, js in enumerate(judge_seconds)
        ],
    )


def test_usage_totals_average_judge_time_over_graded_attempts_only() -> None:
    """An ungraded attempt must not drag the average toward a faster judge."""
    totals = UsageTotals.from_task_results([_task_result([2.0, 4.0, None])])

    assert totals.judge_seconds == 6.0
    assert totals.judged_attempts == 2
    assert totals.attempts == 3


def test_usage_totals_report_no_judge_when_nothing_was_graded() -> None:
    totals = UsageTotals.from_task_results([_task_result([None, None])])

    assert totals.judged_attempts == 0
    assert totals.judge_seconds == 0.0


def _run(interrupted: bool, *, spec: str = "demo", successes: int = 2) -> RunResults:
    return RunResults(
        run=RunMeta(
            spec=spec,
            timestamp=datetime(2026, 7, 3, tzinfo=timezone.utc),
            k=4,
            backend="claude-code",
            era="install-and-discover",
            interrupted=interrupted,
        ),
        skill_snapshots=[],
        task_results=[
            TaskResult(
                task_id="task-001",
                task_name="One",
                attempts=[
                    AttemptRecord(
                        attempt=i + 1,
                        output="ok",
                        duration_seconds=1.0,
                        outcome=Outcome.PASS if i < successes else Outcome.TASK_FAIL,
                    )
                    for i in range(4)
                ],
            )
        ],
        aggregate=AggregateScore(avg_score=successes / 4, per_task=[]),
    )


def test_compare_warns_when_a_side_was_interrupted() -> None:
    comp = diff_runs(_run(interrupted=True), _run(interrupted=False))

    assert any("stopped before every attempt ran" in w for w in comp.warnings)
    assert any(w.startswith("A ") for w in comp.warnings)


def test_compare_names_both_sides_when_both_were_interrupted() -> None:
    comp = diff_runs(_run(interrupted=True), _run(interrupted=True))

    warning = next(w for w in comp.warnings if "stopped before" in w)
    assert warning.startswith("A and B")


def test_compare_is_silent_on_two_complete_runs() -> None:
    """The warning is a provenance fact, so it must not fire on clean runs."""
    comp = diff_runs(_run(interrupted=False), _run(interrupted=False))

    assert not any("stopped before" in w for w in comp.warnings)


def _save(tmp_path: Path, results: RunResults, stem: str) -> None:
    out = tmp_path / ".caliper" / "results" / results.run.spec
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{stem}.json").write_text(results.model_dump_json())


def test_list_marks_an_interrupted_run(monkeypatch, tmp_path) -> None:
    _save(tmp_path, _run(interrupted=True), "2026-07-03T10-00-00Z")
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["list", "demo"])

    assert result.exit_code == 0, result.stdout
    assert "⊘" in result.stdout
    assert "stopped early" in result.stdout


def test_list_is_unmarked_when_every_run_completed(monkeypatch, tmp_path) -> None:
    _save(tmp_path, _run(interrupted=False), "2026-07-03T10-00-00Z")
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["list", "demo"])

    # Asserted on a run that actually rendered: this test only checks an
    # *absence*, so a listing that failed to find anything would pass it.
    assert result.exit_code == 0, result.stdout
    assert "2026-07-03T10-00-00Z" in result.stdout
    assert "stopped early" not in result.stdout


def test_compare_reports_a_regression_without_failing(tmp_path) -> None:
    """`compare` is a reporting command: the regression is rendered, not exited on.

    Its regression flag is the any-below rule, which at small k fires on noise
    as often as on a real change — see docs/CONTEXT.md → Regression. Gating
    belongs on a pre-registered bar, which is what exit 3 is reserved for.
    """
    _save(tmp_path, _run(interrupted=False, successes=4), "a")
    _save(tmp_path, _run(interrupted=False, successes=1), "b")
    base = tmp_path / ".caliper" / "results" / "demo"

    result = runner.invoke(app, ["compare", str(base / "a.json"), str(base / "b.json")])

    assert result.exit_code == 0
