from __future__ import annotations

from caliper.harness.base import AttemptResult, ConversationTurn, HarnessBackend
from caliper.judge.base import JudgeResult
from caliper.outcome import classify_outcome
from caliper.reporter import _is_trigger_only, _status_cell
from caliper.runner import run
from caliper.schema.results import AttemptRecord, Outcome, TaskResult
from caliper.schema.spec import EvalSpec, TaskSpec
from caliper.scoring import score_outcomes


# --- classification -------------------------------------------------------


def _clean() -> AttemptResult:
    return AttemptResult(
        task_id="task-001",
        attempt=1,
        transcript=[],
        final_output="done",
        exit_code=0,
        duration_seconds=0.1,
    )


def test_no_execution_check_is_not_checked_not_judge_error():
    assert (
        classify_outcome(_clean(), [], None, has_execution_check=False)
        is Outcome.NOT_CHECKED
    )


def test_a_missing_verdict_when_a_check_existed_is_still_judge_error():
    assert classify_outcome(_clean(), [], None) is Outcome.JUDGE_ERROR


def test_cheating_outranks_not_checked():
    # A trigger probe that read the answer key is still a cheat.
    assert (
        classify_outcome(_clean(), ["/x/answers.txt"], None, has_execution_check=False)
        is Outcome.CHEAT
    )


def test_infra_failure_outranks_not_checked():
    timed_out = AttemptResult(
        task_id="task-001",
        attempt=1,
        transcript=[],
        final_output="",
        exit_code=124,
        duration_seconds=0.1,
        timed_out=True,
    )
    assert (
        classify_outcome(timed_out, [], None, has_execution_check=False)
        is Outcome.TIMEOUT
    )


# --- scoring --------------------------------------------------------------


def test_not_checked_is_neither_usable_nor_noise():
    assert Outcome.NOT_CHECKED.is_usable is False
    assert Outcome.NOT_CHECKED.is_execution_noise is False
    # But the agent ran, so its activation observation is trustworthy.
    assert Outcome.NOT_CHECKED.is_activation_usable is True


def test_not_checked_does_not_inflate_the_unusable_count():
    scores = score_outcomes([Outcome.NOT_CHECKED] * 3)
    assert scores.usable == 0
    assert scores.unusable == 0
    assert scores.score is None


def test_judge_errors_are_still_counted_as_noise():
    scores = score_outcomes([Outcome.JUDGE_ERROR, Outcome.PASS])
    assert scores.unusable == 1


def test_usable_is_derived_not_subtracted():
    # len - unusable would give 3 here; only the PASS got a fair shot.
    tr = TaskResult(
        task_id="t",
        task_name="t",
        attempts=[
            AttemptRecord(
                attempt=1, output="", duration_seconds=1.0, outcome=Outcome.NOT_CHECKED
            ),
            AttemptRecord(
                attempt=2, output="", duration_seconds=1.0, outcome=Outcome.NOT_CHECKED
            ),
            AttemptRecord(
                attempt=3, output="", duration_seconds=1.0, outcome=Outcome.PASS
            ),
        ],
        successes=1,
        unusable=0,
        pass_at_k=None,
    )
    assert tr.usable == 1
    assert tr.score == 1.0


# --- the runner skips the judge -------------------------------------------


class CleanHarness(HarnessBackend):
    @property
    def name(self) -> str:
        return "clean"

    def run(
        self,
        task_id: str,
        attempt: int,
        prompt: str,
        *,
        skill_refs: list,
        model: str | None,
        timeout: int,
        isolated_home: str,
        extra_path: list[str] | None = None,
        mcp_servers: dict | None = None,
        forbidden_files: list | None = None,
    ) -> AttemptResult:
        return AttemptResult(
            task_id=task_id,
            attempt=attempt,
            transcript=[ConversationTurn(role="assistant", content="Paris.")],
            final_output="Paris.",
            exit_code=0,
            duration_seconds=0.1,
        )


class CountingJudge:
    def __init__(self) -> None:
        self.calls = 0

    def evaluate(self, task, transcript, final_output, spec_dir) -> JudgeResult:
        self.calls += 1
        return JudgeResult(passed=True, reasoning="ok")


def test_runner_skips_the_paid_judge_for_an_activates_only_task(tmp_path):
    spec_path = tmp_path / "s.eval.yaml"
    spec_path.write_text("tasks: []\n")
    judge = CountingJudge()

    results = run(
        spec=EvalSpec(
            skills=[],
            tasks=[
                TaskSpec(
                    id="task-001",
                    name="silence expected",
                    prompt="What is the capital of France?",
                    activates=[],
                )
            ],
        ),
        spec_path=spec_path,
        harness=CleanHarness(),
        judge=judge,
        k=2,
        workers=1,
        timeout=30,
    )

    tr = results.task_results[0]
    assert judge.calls == 0
    assert [a.outcome for a in tr.attempts] == [Outcome.NOT_CHECKED] * 2
    # No execution signal, and crucially no error reported.
    assert tr.score is None
    assert tr.unusable == 0


# --- rendering ------------------------------------------------------------


def _trigger_task() -> TaskResult:
    return TaskResult(
        task_id="t",
        task_name="silence expected",
        attempts=[
            AttemptRecord(
                attempt=n,
                output="",
                duration_seconds=1.0,
                outcome=Outcome.NOT_CHECKED,
                activated=[],
                activation_passed=True,
            )
            for n in (1, 2)
        ],
        successes=0,
        unusable=0,
        pass_at_k=None,
        activation_expected=[],
    )


def test_trigger_only_task_is_detected():
    assert _is_trigger_only(_trigger_task()) is True


def test_trigger_only_task_reads_as_a_skip_not_an_error():
    cell = _status_cell(_trigger_task(), k=2, any_cheat=False)
    assert "UNUSABLE" not in cell.plain
    assert "trigger only" in cell.plain
    assert cell.style == "dim"


def test_a_trigger_probes_tokens_are_not_reported_as_wasted_spend():
    # It spent those tokens producing a real activation measurement.
    from caliper.schema.results import TokenUsage, UsageTotals

    tr = TaskResult(
        task_id="t",
        task_name="t",
        attempts=[
            AttemptRecord(
                attempt=1,
                output="",
                duration_seconds=3.0,
                outcome=Outcome.NOT_CHECKED,
                usage=TokenUsage(input_tokens=24000, output_tokens=10),
            )
        ],
        successes=0,
        unusable=0,
        pass_at_k=None,
    )
    totals = UsageTotals.from_task_results([tr])
    assert totals.unusable_attempts == 0
    assert totals.unusable_tokens == 0
    assert totals.total_tokens == 24010
