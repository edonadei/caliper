from __future__ import annotations

import os
import shlex
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from caliper import cancel
from caliper.harness.base import (
    AttemptResult,
    ConversationTurn,
    HarnessBackend,
    RunContext,
)
from caliper.judge import EvalJudge
from caliper.judge.base import JudgeResult
from caliper.reporter import print_results
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
            transcript=[ConversationTurn(role="assistant", content="judge this")],
            final_output="judge this",
            exit_code=0,
            duration_seconds=0.1,
        )


class RecordingJudge:
    backend = "test"
    model = None

    def __init__(self) -> None:
        self.calls = 0

    def evaluate(
        self, task, transcript, final_output, spec_dir, workdir
    ) -> JudgeResult:
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
            transcript=[ConversationTurn(role="assistant", content="done")],
            final_output="done",
            exit_code=0,
            duration_seconds=0.1,
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
            str(tmp_path),
            dict(os.environ),
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


@pytest.mark.skipif(os.name == "nt", reason="POSIX background shell syntax")
def test_successful_hook_with_continuous_background_writer_returns(tmp_path) -> None:
    pid_file = tmp_path / "writer.pid"
    started = time.monotonic()
    try:
        failure = _run_shell(
            f"yes heartbeat & echo $! > {shlex.quote(str(pid_file))}",
            "task-001",
            1,
            "setup",
            str(tmp_path),
            dict(os.environ),
        )
        assert failure is None
        assert time.monotonic() - started < 3
    finally:
        if pid_file.exists():
            try:
                os.kill(int(pid_file.read_text().strip()), signal.SIGTERM)
            except ProcessLookupError:
                pass


@pytest.mark.skipif(os.name != "nt", reason="Windows background shell syntax")
def test_windows_silent_background_hook_does_not_leak_reader(tmp_path) -> None:
    before = sum(t.name == "caliper-hook-output" for t in threading.enumerate())
    child = subprocess.list2cmdline(
        [sys.executable, "-c", "import time; time.sleep(3)"]
    )
    started = time.monotonic()

    failure = _run_shell(
        f'start /B "" {child}', "task-001", 1, "setup", str(tmp_path), dict(os.environ)
    )

    assert failure is None
    assert time.monotonic() - started < 2
    assert sum(t.name == "caliper-hook-output" for t in threading.enumerate()) == before


def test_noisy_hook_keeps_only_diagnostic_tail(tmp_path) -> None:
    script = (
        'import sys; print("x" * 1000000); '
        'print("last line", file=sys.stderr); sys.exit(7)'
    )
    command = (
        subprocess.list2cmdline([sys.executable, "-c", script])
        if os.name == "nt"
        else f"{shlex.quote(sys.executable)} -c {shlex.quote(script)}"
    )
    failure = _run_shell(
        command,
        "task-001",
        1,
        "setup",
        str(tmp_path),
        dict(os.environ),
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


def test_failed_setup_with_markup_output_still_reports(capfd, tmp_path) -> None:
    task = TaskSpec(
        id="task-001",
        name="Malformed output",
        prompt="Do it",
        assert_script="assert True",
        setup="echo '[/broken]' >&2; exit 7",
    )
    results = run(
        EvalSpec(tasks=[task]),
        tmp_path / "markup.eval.yaml",
        PassingHarness(),
        EvalJudge(),
        k=1,
        workers=1,
    )

    print_results(results)
    output = capfd.readouterr().out
    assert "setup exited 7" in output
    assert "[/broken]" in output


@pytest.mark.skipif(os.name == "nt", reason="POSIX shell and process-group syntax")
def test_cancelling_setup_skips_agent_and_still_runs_cleanup(tmp_path) -> None:
    started = tmp_path / "setup-started"
    cleaned = tmp_path / "cleanup-ran"
    harness = PassingHarness()
    task = TaskSpec(
        id="task-001",
        name="Interrupted setup",
        prompt="Do it",
        assert_script="assert True",
        setup=f"touch {shlex.quote(str(started))}; sleep 5",
        cleanup=f"touch {shlex.quote(str(cleaned))}",
    )
    outcome = {}

    def execute() -> None:
        outcome["results"] = run(
            EvalSpec(tasks=[task]),
            tmp_path / "interrupted.eval.yaml",
            harness,
            EvalJudge(),
            k=1,
            workers=1,
        )

    thread = threading.Thread(target=execute)
    thread.start()
    try:
        deadline = time.monotonic() + 3
        while not started.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert started.exists()
        cancel.request()
        thread.join(timeout=2)
        assert not thread.is_alive()
        assert cleaned.exists()
        assert harness.calls == 0
        assert outcome["results"].run.interrupted is True
        assert outcome["results"].task_results[0].attempts == []
    finally:
        if thread.is_alive():
            cancel.request()
            thread.join(timeout=6)


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

    def evaluate(
        self, task, transcript, final_output, spec_dir, workdir
    ) -> JudgeResult:
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
            transcript=[ConversationTurn(role="assistant", content="done")],
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

    def evaluate(
        self, task, transcript, final_output, spec_dir, workdir
    ) -> JudgeResult:
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


def test_runmeta_records_resolved_model_over_requested_and_warns(tmp_path) -> None:
    """RunMeta names what ran, not what was asked for (#131).

    A backend that ignored the requested model would otherwise leave a saved run
    claiming a model that never ran, and a model-vs-model ``compare`` comparing
    the default against itself.
    """
    spec_path = tmp_path / "prov.eval.yaml"
    spec_path.write_text("tasks: []\n")
    warnings: list[str] = []

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
        on_warning=warnings.append,
    )

    assert results.run.model == "some/other-model"
    assert len(warnings) == 1
    assert "anthropic/claude-sonnet-4.6" in warnings[0]
    assert "some/other-model" in warnings[0]


class RotatingModelHarness(HarnessBackend):
    """Reports a different resolved model per attempt, in the given order."""

    def __init__(self, resolved_models: list[str], model: str | None = None) -> None:
        self._resolved = iter(resolved_models)
        self._model = model

    @property
    def name(self) -> str:
        return "rotating"

    def run(self, ctx: RunContext) -> AttemptResult:
        return AttemptResult(
            transcript=[],
            final_output="done",
            exit_code=0,
            duration_seconds=0.1,
            resolved_model=next(self._resolved),
        )


def test_runmeta_records_the_majority_model_and_warns_on_a_mixed_run(
    tmp_path,
) -> None:
    # Which attempt finishes first is timing, so it must not pick the model.
    spec_path = tmp_path / "prov.eval.yaml"
    spec_path.write_text("tasks: []\n")
    warnings: list[str] = []

    results = run(
        spec=_one_task_spec(),
        spec_path=spec_path,
        harness=RotatingModelHarness(
            ["provider/model-b", "provider/model-a", "provider/model-a"],
            model="provider/model-a",
        ),
        judge=RecordingJudge(),
        k=3,
        workers=1,
        timeout=30,
        on_warning=warnings.append,
    )

    assert results.run.model == "provider/model-a"
    assert len(warnings) == 1
    assert "provider/model-a" in warnings[0]
    assert "provider/model-b" in warnings[0]


class CancelledAfterFirstHarness(HarnessBackend):
    """Attempt 1 completes on ``actual``; every later one is killed by Ctrl-C."""

    def __init__(self, actual: str, model: str) -> None:
        self._actual = actual
        self._model = model
        self._calls = 0

    @property
    def name(self) -> str:
        return "cancelling"

    def run(self, ctx: RunContext) -> AttemptResult:
        self._calls += 1
        first = self._calls == 1
        return AttemptResult(
            transcript=[],
            final_output="done" if first else "",
            exit_code=0 if first else -1,
            duration_seconds=0.1,
            # A killed attempt has no export to read, so it falls back to the
            # requested model.
            resolved_model=self._actual if first else self._model,
            cancelled=not first,
        )


def test_runmeta_ignores_the_models_of_cancelled_attempts(tmp_path) -> None:
    # A cancelled attempt is discarded, so its fallback model must not outvote
    # the one attempt that actually ran.
    spec_path = tmp_path / "prov.eval.yaml"
    spec_path.write_text("tasks: []\n")

    results = run(
        spec=_one_task_spec(),
        spec_path=spec_path,
        harness=CancelledAfterFirstHarness("provider/model-b", "provider/model-a"),
        judge=RecordingJudge(),
        k=3,
        workers=1,
        timeout=30,
    )

    assert results.run.model == "provider/model-b"


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


class WorkdirHarness(HarnessBackend):
    """Writes into its cwd, and reports what ``setup:`` left there."""

    def __init__(self) -> None:
        self.saw_setup_marker: bool | None = None
        self.workdir: str | None = None

    @property
    def name(self) -> str:
        return "workdir"

    def run(self, ctx: RunContext) -> AttemptResult:
        workdir = Path(ctx.workdir)
        self.workdir = ctx.workdir
        self.saw_setup_marker = (workdir / "SETUP_MARKER").exists()
        (workdir / "out.txt").write_text("banana")
        return AttemptResult(
            transcript=[ConversationTurn(role="assistant", content="done")],
            final_output="done",
            exit_code=0,
            duration_seconds=0.1,
        )


def test_agent_setup_and_assert_share_one_attempt_workdir(
    monkeypatch, tmp_path
) -> None:
    # Issue #130: the agent, `setup:` and `assert:` each ran somewhere else.
    launch_dir = tmp_path / "launch"
    launch_dir.mkdir()
    monkeypatch.chdir(launch_dir)
    spec_dir = tmp_path / "spec"
    spec_dir.mkdir()
    (spec_dir / "fixture.txt").write_text("from the spec dir")
    task = TaskSpec(
        id="task-001",
        name="Writes a file relative to its cwd",
        prompt="Create out.txt",
        setup="touch SETUP_MARKER",
        cleanup="touch CLEANUP_MARKER",
        assert_script=(
            "import os\n"
            "from pathlib import Path\n"
            "assert Path('out.txt').read_text() == 'banana'\n"
            "assert Path('SETUP_MARKER').exists()\n"
            "assert Path.cwd() == Path(os.environ['CALIPER_WORKDIR']).resolve()\n"
            "spec_dir = Path(os.environ['CALIPER_SPEC_DIR'])\n"
            "assert (spec_dir / 'fixture.txt').read_text() == 'from the spec dir'\n"
        ),
    )
    harness = WorkdirHarness()

    results = run(
        EvalSpec(tasks=[task]),
        spec_dir / "workdir.eval.yaml",
        harness,
        EvalJudge(),
        k=1,
        workers=1,
    )

    record = results.task_results[0].attempts[0]
    assert harness.saw_setup_marker is True
    assert record.outcome is Outcome.PASS, record.assert_evidence
    assert record.hook_failures == []
    assert list(launch_dir.iterdir()) == []
    assert sorted(p.name for p in spec_dir.iterdir()) == ["fixture.txt"]
    # The workdir is per-attempt scratch, gone once the attempt is recorded.
    assert harness.workdir is not None and not Path(harness.workdir).exists()


def test_hooks_see_the_workdir_and_spec_dir_env(tmp_path) -> None:
    seen = tmp_path / "seen"
    task = TaskSpec(
        id="task-001",
        name="Hook env",
        prompt="Do it",
        assert_script="assert True",
        setup=f'echo "$PWD|$CALIPER_WORKDIR|$CALIPER_SPEC_DIR" > {seen}',
    )
    harness = WorkdirHarness()

    run(
        EvalSpec(tasks=[task]),
        tmp_path / "env.eval.yaml",
        harness,
        EvalJudge(),
        k=1,
        workers=1,
    )

    pwd, workdir, spec_dir = seen.read_text().strip().split("|")
    assert Path(pwd).resolve() == Path(workdir).resolve()
    assert workdir == harness.workdir
    assert Path(spec_dir) == tmp_path


def test_spec_dir_env_is_absolute_for_a_relative_spec_path(
    monkeypatch, tmp_path
) -> None:
    # `caliper run evals/app.eval.yaml` hands the runner a relative path; the
    # hooks run in the workdir, where a relative CALIPER_SPEC_DIR names nothing.
    monkeypatch.chdir(tmp_path)
    (tmp_path / "evals").mkdir()
    (tmp_path / "evals" / "input.txt").write_text("fixture")
    task = TaskSpec(
        id="task-001",
        name="Copies a fixture",
        prompt="Do it",
        setup='cp "$CALIPER_SPEC_DIR/input.txt" .',
        assert_script=(
            "import os\n"
            "from pathlib import Path\n"
            "assert Path('input.txt').read_text() == 'fixture'\n"
            "assert Path(os.environ['CALIPER_SPEC_DIR']).is_absolute()\n"
        ),
    )

    results = run(
        EvalSpec(tasks=[task]),
        Path("evals/app.eval.yaml"),
        WorkdirHarness(),
        EvalJudge(),
        k=1,
        workers=1,
    )

    record = results.task_results[0].attempts[0]
    assert record.hook_failures == []
    assert record.outcome is Outcome.PASS, record.assert_evidence
