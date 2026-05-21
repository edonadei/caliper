from __future__ import annotations

from verdict.harness.base import AttemptResult, HarnessBackend
from verdict.judge.base import Judge, JudgeResult
from verdict.runner import TaskRunner
from verdict.schema.spec import EvalSpec, SkillConfig, TaskSpec


class FailingHarness(HarnessBackend):
    @property
    def name(self) -> str:
        return "failing"

    def run(
        self,
        task_id: str,
        attempt: int,
        prompt: str,
        *,
        skill_path: str | None,
        model: str | None,
        timeout: int,
        isolated_home: str,
        extra_path: list[str] | None = None,
    ) -> AttemptResult:
        return AttemptResult(
            task_id=task_id,
            attempt=attempt,
            transcript=[],
            final_output="",
            exit_code=1,
            duration_seconds=0.1,
            error="agent failed",
        )


class RecordingJudge(Judge):
    def __init__(self) -> None:
        self.calls = 0

    def evaluate(self, task, transcript, final_output, spec_dir) -> JudgeResult:
        self.calls += 1
        return JudgeResult(passed=True, reasoning="should not run")


def test_runner_fails_attempt_when_harness_exits_nonzero(tmp_path) -> None:
    spec_path = tmp_path / "failing.eval.yaml"
    spec_path.write_text("skill:\n  backend: codex\ntasks: []\n")
    judge = RecordingJudge()
    spec = EvalSpec(
        skill=SkillConfig(backend="codex"),
        tasks=[
            TaskSpec(
                id="task-001",
                name="Harness failure",
                prompt="Do the thing",
                assert_script="assert True",
            )
        ],
    )

    results = TaskRunner(
        harness=FailingHarness(),
        judge=judge,
        spec=spec,
        spec_path=spec_path,
        k=1,
        workers=1,
        timeout=30,
    ).run()

    attempt = results.task_results[0].attempts[0]
    assert attempt.passed is False
    assert attempt.assert_passed is False
    assert attempt.assert_evidence == "agent failed"
    assert results.aggregate.avg_pass_at_k == 0
    assert judge.calls == 0
