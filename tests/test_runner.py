from __future__ import annotations

import ast
import os
from pathlib import Path

from caliper.harness.base import (
    AttemptResult,
    ConversationTurn,
    HarnessBackend,
    RunContext,
)
from caliper.judge import EvalJudge
from caliper.judge.base import JudgeResult
from caliper.runner import run
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

    def evaluate(self, task, transcript, final_output, spec_dir, attempt_dir=None) -> JudgeResult:
        self.calls += 1
        return JudgeResult(passed=True, reasoning="should not run")


class JudgeErrorThenPass:
    backend = "test"
    model = None

    def __init__(self) -> None:
        self.calls = 0

    def evaluate(self, task, transcript, final_output, spec_dir, attempt_dir=None) -> JudgeResult:
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

    def evaluate(self, task, transcript, final_output, spec_dir, attempt_dir=None) -> JudgeResult:
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


class SetupProbeHarness(HarnessBackend):
    """Records what the attempt's setup staged inside the attempt home.

    Keyed by attempt number: with ``workers >= 2`` two run() calls interleave,
    so append-order pairing of two lists would match one attempt's env with
    another's home.
    """

    def __init__(self) -> None:
        self.homes: dict[int, str] = {}
        self.cwd_seen: dict[int, str | None] = {}
        self.envs: dict[int, dict[str, str]] = {}

    @property
    def name(self) -> str:
        return "setup-probe"

    def run(self, ctx: RunContext) -> AttemptResult:
        home = Path(ctx.isolated_home)
        self.homes[ctx.attempt] = ctx.isolated_home
        marker = home / "setup-marker.txt"
        self.cwd_seen[ctx.attempt] = (
            marker.read_text().strip() if marker.exists() else None
        )
        env_file = home / "setup-env.txt"
        self.envs[ctx.attempt] = (
            ast.literal_eval(env_file.read_text()) if env_file.exists() else {}
        )
        return AttemptResult(
            transcript=[], final_output="ok", exit_code=0, duration_seconds=0.1
        )


def test_setup_runs_inside_the_attempt_home(tmp_path) -> None:
    """A parallel attempt's setup cannot write into the caller's directory.

    Setups used to shell out from caliper's own CWD, so k workers raced on
    one shared directory (and could drop artifacts into whatever repository
    caliper was invoked from). The setup now runs in the attempt's isolated
    home — the same boundary the agent itself gets — with the boundaries a
    spec may legitimately reach for named in ``CALIPER_*`` env vars.
    """
    spec_path = tmp_path / "setup.eval.yaml"
    spec_path.write_text("tasks: []\n")
    task = TaskSpec(
        id="task-001",
        name="Setup probe",
        prompt="p",
        expect="ok",
        setup=(
            "python -c \"import os; open('setup-marker.txt', 'w').write(os.getcwd())\" && "
            "python -c \"import os; open('setup-env.txt', 'w').write(repr({k: v for k, v in os.environ.items() if k.startswith('CALIPER_')}))\""
        ),
    )
    harness = SetupProbeHarness()

    results = run(
        spec=EvalSpec(tasks=[task]),
        spec_path=spec_path,
        harness=harness,
        judge=RecordingJudge(),
        k=2,
        workers=2,
        timeout=30,
    )

    assert [a.outcome for a in results.task_results[0].attempts] == [Outcome.PASS] * 2
    assert sorted(harness.homes) == [1, 2]
    # Each attempt's setup ran with the attempt's own home as its CWD —
    # the marker (written to a relative path) sits where the agent ran,
    # and its contents name that same directory. Path comparison, not string:
    # Windows getcwd() normalization may differ in case from the cwd= input.
    for attempt in (1, 2):
        cwd, home = harness.cwd_seen[attempt], harness.homes[attempt]
        assert cwd is not None and os.path.normcase(cwd) == os.path.normcase(home), (
            f"attempt {attempt}: setup CWD left the attempt home: {cwd!r} vs {home!r}"
        )
    assert len({os.path.normcase(c) for c in harness.cwd_seen.values() if c}) == 2
    # The boundaries a spec may reach for are named, per attempt.
    assert all(
        e["CALIPER_TASK_ID"] == "task-001" for e in harness.envs.values()
    )
    assert sorted(harness.envs) == [1, 2]
    for attempt in (1, 2):
        assert os.path.normcase(
            harness.envs[attempt]["CALIPER_ATTEMPT_DIR"]
        ) == os.path.normcase(harness.homes[attempt]), (
            f"attempt {attempt}: ATTEMPT_DIR {harness.envs[attempt]} "
            f"vs home {harness.homes[attempt]!r}"
        )
        assert os.path.normcase(
            harness.envs[attempt]["CALIPER_SPEC_DIR"]
        ) == os.path.normcase(str(tmp_path))
