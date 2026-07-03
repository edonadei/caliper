from __future__ import annotations

from caliper.harness.base import AttemptResult, HarnessBackend
from caliper.judge.base import Judge, JudgeResult
from caliper.runner import _stage_skill_directory, run
from caliper.schema.results import Outcome
from caliper.schema.spec import EvalSpec, SkillConfig, TaskSpec


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


class InfraErrorHarness(FailingHarness):
    def __init__(self) -> None:
        self.attempts: list[int] = []

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
        self.attempts.append(attempt)
        return super().run(
            task_id=task_id,
            attempt=attempt,
            prompt=prompt,
            skill_path=skill_path,
            model=model,
            timeout=timeout,
            isolated_home=isolated_home,
            extra_path=extra_path,
        )


class MixedOutcomeHarness(HarnessBackend):
    def __init__(self) -> None:
        self.attempts: list[int] = []

    @property
    def name(self) -> str:
        return "mixed"

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
        self.attempts.append(attempt)
        if attempt in (1, 3):
            return AttemptResult(
                task_id=task_id,
                attempt=attempt,
                transcript=[],
                final_output="",
                exit_code=1,
                duration_seconds=0.1,
                error="agent failed",
            )
        return AttemptResult(
            task_id=task_id,
            attempt=attempt,
            transcript=[],
            final_output="judge this",
            exit_code=0,
            duration_seconds=0.1,
        )


class RecordingJudge(Judge):
    def __init__(self) -> None:
        self.calls = 0

    def evaluate(self, task, transcript, final_output, spec_dir) -> JudgeResult:
        self.calls += 1
        return JudgeResult(passed=True, reasoning="should not run")


class JudgeErrorThenPass(Judge):
    def __init__(self) -> None:
        self.calls = 0

    def evaluate(self, task, transcript, final_output, spec_dir) -> JudgeResult:
        self.calls += 1
        if self.calls == 1:
            return JudgeResult(passed=False, reasoning="judge flaked", errored=True)
        return JudgeResult(passed=True, reasoning="ok")


def _one_task_spec() -> EvalSpec:
    return EvalSpec(
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


def test_runner_fails_attempt_when_harness_exits_nonzero(tmp_path) -> None:
    spec_path = tmp_path / "failing.eval.yaml"
    spec_path.write_text("skill:\n  backend: codex\ntasks: []\n")
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
    spec_path.write_text("skill:\n  backend: codex\ntasks: []\n")
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
    spec_path.write_text("skill:\n  backend: codex\ntasks: []\n")
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
    spec_path.write_text("skill:\n  backend: codex\ntasks: []\n")
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
    spec_path.write_text("skill:\n  backend: codex\ntasks: []\n")
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


def _make_skill_dir(tmp_path):
    skill_dir = tmp_path / "my-skill"
    (skill_dir / "references").mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("See [REFERENCE.md](REFERENCE.md)")
    (skill_dir / "REFERENCE.md").write_text("token: UNIQUE-REF-42")
    (skill_dir / "references" / "deep.md").write_text("nested detail")
    return skill_dir


def test_stage_copies_skill_siblings_into_home(tmp_path) -> None:
    skill_dir = _make_skill_dir(tmp_path)
    home = tmp_path / "home"
    home.mkdir()

    _stage_skill_directory(str(skill_dir / "SKILL.md"), str(home), [])

    assert (home / "REFERENCE.md").read_text() == "token: UNIQUE-REF-42"
    assert (home / "references" / "deep.md").read_text() == "nested detail"
    assert (home / "SKILL.md").exists()


def test_stage_excludes_cheat_surfaces(tmp_path) -> None:
    skill_dir = _make_skill_dir(tmp_path)
    (skill_dir / ".caliper" / "results").mkdir(parents=True)
    (skill_dir / ".caliper" / "results" / "run.json").write_text("answers")
    (skill_dir / "my-skill.eval.yaml").write_text("expect: the secret")
    (skill_dir / "secret.txt").write_text("do not stage me")
    home = tmp_path / "home"
    home.mkdir()

    _stage_skill_directory(
        str(skill_dir / "SKILL.md"), str(home), [r"secret\.txt$"]
    )

    assert not (home / ".caliper").exists()
    assert not (home / "my-skill.eval.yaml").exists()
    assert not (home / "secret.txt").exists()
    # Legitimate references are still staged.
    assert (home / "REFERENCE.md").exists()


def test_stage_ignores_lone_command_file(tmp_path) -> None:
    # A bare slash-command .md (not named SKILL.md) has no skill directory;
    # we must not slurp its siblings (which could be an arbitrary repo).
    (tmp_path / "review.md").write_text("Review the code.")
    (tmp_path / "unrelated.md").write_text("do not copy")
    home = tmp_path / "home"
    home.mkdir()

    _stage_skill_directory(str(tmp_path / "review.md"), str(home), [])

    assert not (home / "unrelated.md").exists()
