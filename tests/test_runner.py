from __future__ import annotations

import os
import shlex
import signal
import sys
import time

import pytest

from caliper.harness.base import (
    AttemptResult,
    ConversationTurn,
    HarnessBackend,
    RunContext,
)
from caliper.judge import EvalJudge
from caliper.judge.base import JudgeResult
from caliper.runner import _run_shell, run
from caliper.schema.results import Outcome
from caliper.schema.spec import EvalSpec, TaskSpec


class FailingHarness(HarnessBackend):
    @property
    def name(self) -> str:
        return "failing"

    def run(self, ctx: RunContext) -> AttemptResult:
        return AttemptResult(
            transcript=[],
            final_output="",
            exit_code=1,
            duration_seconds=0.1,
            error="agent failed",
        )


class InfraErrorHarness(FailingHarness):
    def __init__(self) -> None:
        self.attempts: list[int] = []

    def run(self, ctx: RunContext) -> AttemptResult:
        self.attempts.append(ctx.attempt)
        return super().run(ctx)


class MixedOutcomeHarness(HarnessBackend):
    def __init__(self) -> None:
        self.attempts: list[int] = []

    @property
    def name(self) -> str:
        return "mixed"

    def run(self, ctx: RunContext) -> AttemptResult:
        self.attempts.append(ctx.attempt)
        if ctx.attempt in (1, 3):
            return AttemptResult(
                transcript=[],
                final_output="",
                exit_code=1,
                duration_seconds=0.1,
                error="agent failed",
            )
        return AttemptResult(
            transcript=[],
            final_output="judge this",
            exit_code=0,
            duration_seconds=0.1,
        )


class RecordingJudge:
    backend = "test"
    model = None

    def __init__(self) -> None:
        self.calls = 0

    def evaluate(self, task, transcript, final_output, spec_dir) -> JudgeResult:
        self.calls += 1
        return JudgeResult(passed=True, reasoning="should not run")


class PassingHarness(HarnessBackend):
    def __init__(self) -> None:
        self.calls = 0

    @property
    def name(self) -> str:
        return "passing"

    def run(self, ctx: RunContext) -> AttemptResult:
        self.calls += 1
        return AttemptResult(
            transcript=[], final_output="done", exit_code=0, duration_seconds=0.1
        )


@pytest.mark.skipif(os.name == "nt", reason="POSIX background shell syntax")
def test_hook_with_background_child_returns_after_its_shell_exits(tmp_path) -> None:
    pid_file = tmp_path / "background.pid"
    started = time.monotonic()
    try:
        failure = _run_shell(
            f"sleep 5 & echo $! > {shlex.quote(str(pid_file))}; "
            "echo setup broke >&2; exit 7",
            "task-001",
            1,
            "setup",
        )
        assert time.monotonic() - started < 3
        assert failure is not None
        assert failure.exit_code == 7
        assert failure.output == "setup broke"
    finally:
        if pid_file.exists():
            try:
                os.kill(int(pid_file.read_text().strip()), signal.SIGTERM)
            except ProcessLookupError:
                pass


def test_noisy_hook_keeps_only_diagnostic_tail() -> None:
    failure = _run_shell(
        f"{shlex.quote(sys.executable)} -c "
        '\'import sys; print("x" * 1000000); '
        'print("last line", file=sys.stderr); sys.exit(7)\'',
        "task-001",
        1,
        "setup",
    )

    assert failure is not None
    assert failure.exit_code == 7
    assert failure.output.endswith("last line")
    assert len(failure.output) <= 4000


def test_failed_setup_cannot_pass_from_stale_artifact_and_still_cleans_up(
    tmp_path,
) -> None:
    artifact = tmp_path / "artifact.txt"
    artifact.write_text("stale")
    cleaned = tmp_path / "cleaned"
    task = TaskSpec(
        id="task-001",
        name="Stale artifact",
        prompt="Do it",
        assert_script="from pathlib import Path\nassert Path('artifact.txt').exists()",
        setup="echo setup broke >&2; exit 7",
        cleanup=f"touch {cleaned}",
    )
    harness = PassingHarness()
    judge = EvalJudge()
    results = run(
        EvalSpec(tasks=[task]),
        tmp_path / "stale.eval.yaml",
        harness,
        judge,
        k=1,
        workers=1,
    )

    record = results.task_results[0].attempts[0]
    assert harness.calls == 0
    assert record.outcome is Outcome.INFRA_ERROR
    assert record.passed is False
    assert record.assert_passed is None
    assert results.task_results[0].score is None
    assert cleaned.exists()
    assert [(f.phase, f.exit_code, f.output) for f in record.hook_failures] == [
        ("setup", 7, "setup broke")
    ]
    assert results.run.hook_failures == record.hook_failures


def test_failed_cleanup_keeps_agent_outcome_and_reports_both_hook_failures(
    tmp_path,
) -> None:
    task = TaskSpec(
        id="task-001",
        name="Cleanup failure",
        prompt="Do it",
        assert_script="assert True",
        setup="echo setup broke >&2; exit 7",
        cleanup="echo cleanup broke >&2; exit 9",
    )
    results = run(
        EvalSpec(tasks=[task]),
        tmp_path / "hooks.eval.yaml",
        PassingHarness(),
        EvalJudge(),
        k=1,
        workers=1,
    )
    assert results.task_results[0].attempts[0].outcome is Outcome.INFRA_ERROR
    assert [(f.phase, f.exit_code) for f in results.run.hook_failures] == [
        ("setup", 7),
        ("cleanup", 9),
    ]

    task.setup = None
    passed = run(
        EvalSpec(tasks=[task]),
        tmp_path / "hooks.eval.yaml",
        PassingHarness(),
        EvalJudge(),
        k=1,
        workers=1,
    )
    record = passed.task_results[0].attempts[0]
    assert record.outcome is Outcome.PASS
    assert record.hook_failures[0].phase == "cleanup"
    assert passed.run.hook_failures == record.hook_failures


@pytest.mark.parametrize(
    ("result_fields", "expected_outcome"),
    [
        ({"exit_code": 1, "error": "agent broke"}, Outcome.INFRA_ERROR),
        ({"exit_code": -9, "timed_out": True}, Outcome.TIMEOUT),
        ({"exit_code": -9, "cancelled": True}, None),
    ],
)
def test_cleanup_runs_after_failed_timed_out_or_cancelled_agent(
    tmp_path, result_fields, expected_outcome
) -> None:
    cleaned = tmp_path / "cleaned"

    class StoppingHarness(HarnessBackend):
        @property
        def name(self) -> str:
            return "stopping"

        def run(self, ctx: RunContext) -> AttemptResult:
            return AttemptResult(
                transcript=[], final_output="", duration_seconds=0.1, **result_fields
            )

    task = TaskSpec(
        id="task-001",
        name="Stopped",
        prompt="Do it",
        assert_script="assert True",
        cleanup=f"touch {cleaned}; echo cleanup broke >&2; exit 9",
    )
    results = run(
        EvalSpec(tasks=[task]),
        tmp_path / "stopped.eval.yaml",
        StoppingHarness(),
        EvalJudge(),
        k=1,
        workers=1,
    )

    assert cleaned.exists()
    assert results.run.hook_failures[0].phase == "cleanup"
    assert results.run.hook_failures[0].exit_code == 9
    records = results.task_results[0].attempts
    if expected_outcome is None:
        assert records == []
    else:
        assert records[0].outcome is expected_outcome
        assert records[0].hook_failures == results.run.hook_failures


class JudgeErrorThenPass:
    backend = "test"
    model = None

    def __init__(self) -> None:
        self.calls = 0

    def evaluate(self, task, transcript, final_output, spec_dir) -> JudgeResult:
        self.calls += 1
        if self.calls == 1:
            return JudgeResult(passed=False, reasoning="judge flaked", errored=True)
        return JudgeResult(passed=True, reasoning="ok")


def _one_task_spec() -> EvalSpec:
    return EvalSpec(
        tasks=[
            TaskSpec(
                id="task-001",
                name="Harness failure",
                prompt="Do the thing",
                assert_script="assert True",
            )
        ],
    )


def test_runner_fails_attempt_when_harness_exits_nonzero(tmp_path) -> None:
    spec_path = tmp_path / "failing.eval.yaml"
    spec_path.write_text("tasks: []\n")
    judge = RecordingJudge()
    spec = _one_task_spec()

    results = run(
        spec=spec,
        spec_path=spec_path,
        harness=FailingHarness(),
        judge=judge,
        k=1,
        workers=1,
        timeout=30,
    )

    attempt = results.task_results[0].attempts[0]
    # A nonzero harness exit is infrastructure noise, not a task failure: it is
    # unusable, excluded from pass@k, and never reaches the judge.
    assert attempt.outcome is Outcome.INFRA_ERROR
    assert attempt.passed is False
    assert attempt.assert_evidence == "agent failed"
    tr = results.task_results[0]
    assert tr.unusable == 1
    assert tr.pass_at_k is None
    assert judge.calls == 0


def test_runner_runs_all_infra_failures_by_default(tmp_path) -> None:
    spec_path = tmp_path / "failing.eval.yaml"
    spec_path.write_text("tasks: []\n")
    harness = InfraErrorHarness()

    results = run(
        spec=_one_task_spec(),
        spec_path=spec_path,
        harness=harness,
        judge=RecordingJudge(),
        k=3,
        workers=1,
        timeout=30,
    )

    assert harness.attempts == [1, 2, 3]
    assert len(results.task_results[0].attempts) == 3
    assert results.task_results[0].unusable == 3
    assert results.task_results[0].pass_at_k is None


def test_runner_fail_fast_stops_after_unusable_threshold(tmp_path) -> None:
    spec_path = tmp_path / "failing.eval.yaml"
    spec_path.write_text("tasks: []\n")
    harness = InfraErrorHarness()

    results = run(
        spec=_one_task_spec(),
        spec_path=spec_path,
        harness=harness,
        judge=RecordingJudge(),
        k=3,
        workers=1,
        timeout=30,
        fail_fast_unusable=1,
    )

    task = results.task_results[0]
    assert harness.attempts == [1]
    assert [attempt.outcome for attempt in task.attempts] == [Outcome.INFRA_ERROR]
    assert task.unusable == 1
    assert task.pass_at_k is None


def test_runner_fail_fast_does_not_reset_streak_on_judge_error(tmp_path) -> None:
    spec_path = tmp_path / "failing.eval.yaml"
    spec_path.write_text("tasks: []\n")
    harness = MixedOutcomeHarness()

    results = run(
        spec=_one_task_spec(),
        spec_path=spec_path,
        harness=harness,
        judge=JudgeErrorThenPass(),
        k=4,
        workers=1,
        timeout=30,
        fail_fast_unusable=2,
    )

    task = results.task_results[0]
    assert harness.attempts == [1, 2, 3]
    assert [attempt.outcome for attempt in task.attempts] == [
        Outcome.INFRA_ERROR,
        Outcome.JUDGE_ERROR,
        Outcome.INFRA_ERROR,
    ]
    assert task.unusable == 3
    assert task.pass_at_k is None


def test_runner_emits_task_done_when_fail_fast_stops_early(tmp_path) -> None:
    spec_path = tmp_path / "failing.eval.yaml"
    spec_path.write_text("tasks: []\n")
    finished_tasks = []

    run(
        spec=_one_task_spec(),
        spec_path=spec_path,
        harness=InfraErrorHarness(),
        judge=RecordingJudge(),
        k=3,
        workers=1,
        timeout=30,
        fail_fast_unusable=1,
        on_task_done=finished_tasks.append,
    )

    assert len(finished_tasks) == 1
    assert finished_tasks[0].task_id == "task-001"
    assert len(finished_tasks[0].attempts) == 1
    assert finished_tasks[0].pass_at_k is None


class ResolvedModelHarness(HarnessBackend):
    """A harness that reports the concrete model it resolved for each attempt.

    ``model`` is what it was *built* with (``None`` = the CLI's own default);
    ``resolved_model`` is what actually ran.
    """

    def __init__(self, resolved_model: str, model: str | None = None) -> None:
        self._resolved = resolved_model
        self._model = model

    @property
    def name(self) -> str:
        return "resolving"

    def run(self, ctx: RunContext) -> AttemptResult:
        return AttemptResult(
            transcript=[],
            final_output="done",
            exit_code=0,
            duration_seconds=0.1,
            resolved_model=self._resolved,
        )


class ModelReportingJudge:
    """A judge that reports the concrete model its autorater resolved."""

    def __init__(
        self, resolved_model: str, *, backend: str = "test", model: str | None = None
    ) -> None:
        self._resolved = resolved_model
        self.backend = backend
        self.model = model

    def evaluate(self, task, transcript, final_output, spec_dir) -> JudgeResult:
        return JudgeResult(passed=True, reasoning="ok", resolved_model=self._resolved)


def test_runmeta_records_judge_engine_and_resolved_model(tmp_path) -> None:
    spec_path = tmp_path / "prov.eval.yaml"
    spec_path.write_text("tasks: []\n")

    results = run(
        spec=_one_task_spec(),
        spec_path=spec_path,
        # No skill model requested — the backend's resolved model should fill it.
        harness=ResolvedModelHarness("stepfun/step-3.7-flash:free"),
        judge=ModelReportingJudge(
            "anthropic/claude-sonnet-4.6",
            backend="hermes",
            model="anthropic/claude-sonnet-4.6",
        ),
        k=1,
        workers=1,
        timeout=30,
    )

    # The judge engine that graded the run is persisted for reproducibility.
    assert results.run.judge_backend == "hermes"
    assert results.run.judge_model == "anthropic/claude-sonnet-4.6"
    # A default-model run still records the concrete model that actually ran.
    assert results.run.model == "stepfun/step-3.7-flash:free"


def test_runmeta_fills_default_judge_model_from_autorater(tmp_path) -> None:
    spec_path = tmp_path / "prov.eval.yaml"
    spec_path.write_text("tasks: []\n")

    results = run(
        spec=_one_task_spec(),
        spec_path=spec_path,
        harness=ResolvedModelHarness("some/model"),
        # No judge model requested — the autorater's concrete model fills it.
        judge=ModelReportingJudge("claude-opus-4-8", backend="claude-code"),
        k=1,
        workers=1,
        timeout=30,
    )

    assert results.run.judge_backend == "claude-code"
    assert results.run.judge_model == "claude-opus-4-8"


def test_runmeta_records_no_judge_model_when_no_autorater_ran(tmp_path) -> None:
    """An assert-only run names no judge model: nothing graded it but a script.

    ``EvalJudge`` applies a pinned default when the caller omits ``--judge-model``,
    but only at the moment it calls an autorater. Recording that default on a
    run that never made the call would claim a model graded work it never saw.
    """
    spec_path = tmp_path / "prov.eval.yaml"
    spec_path.write_text("tasks: []\n")

    results = run(
        spec=_one_task_spec(),
        spec_path=spec_path,
        harness=ResolvedModelHarness("some/model"),
        judge=EvalJudge(backend="claude-code"),
        k=1,
        workers=1,
        timeout=30,
    )

    assert results.run.judge_backend == "claude-code"
    assert results.run.judge_model is None


def test_runmeta_prefers_explicit_model_over_resolved(tmp_path) -> None:
    spec_path = tmp_path / "prov.eval.yaml"
    spec_path.write_text("tasks: []\n")

    results = run(
        spec=_one_task_spec(),
        spec_path=spec_path,
        harness=ResolvedModelHarness(
            "some/other-model", model="anthropic/claude-sonnet-4.6"
        ),
        judge=RecordingJudge(),
        k=1,
        workers=1,
        timeout=30,
    )

    assert results.run.model == "anthropic/claude-sonnet-4.6"


class TranscriptHarness(HarnessBackend):
    @property
    def name(self) -> str:
        return "transcript"

    def run(self, ctx: RunContext) -> AttemptResult:
        return AttemptResult(
            transcript=[
                ConversationTurn(role="assistant", content="calling tool"),
                ConversationTurn(
                    role="tool_use",
                    content="[tool: mcp__wiki__read]",
                    tool_name="mcp__wiki__read",
                    tool_input={"page": "home"},
                ),
                ConversationTurn(
                    role="tool_result",
                    content="ok",
                    tool_name="mcp__wiki__read",
                    tool_output="ok",
                ),
            ],
            final_output="done",
            exit_code=0,
            duration_seconds=0.2,
        )


def test_runner_persists_attempt_transcript(tmp_path) -> None:
    spec_path = tmp_path / "transcript.eval.yaml"
    spec_path.write_text("tasks: []\n")

    results = run(
        spec=_one_task_spec(),
        spec_path=spec_path,
        harness=TranscriptHarness(),
        judge=RecordingJudge(),
        k=1,
        workers=1,
        timeout=30,
    )

    attempt = results.task_results[0].attempts[0]
    assert attempt.transcript is not None
    assert len(attempt.transcript) == 3
    assert attempt.transcript[1].tool_name == "mcp__wiki__read"
    assert attempt.transcript[1].tool_input == {"page": "home"}
    assert attempt.transcript[2].tool_output == "ok"
