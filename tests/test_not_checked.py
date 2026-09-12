from __future__ import annotations

from datetime import datetime, timezone

from typer.testing import CliRunner

from caliper.commands.list_cmd import _score_cell
from caliper.reporter import RULE_GLYPH, UNUSABLE_GLYPH
from caliper.harness.base import AttemptResult, ConversationTurn, HarnessBackend
from caliper.judge.base import JudgeResult
from caliper.outcome import classify_outcome
from caliper.reporter import _status_cell
from caliper.main import app
from caliper.runner import run
from caliper.schema.results import (
    AggregateScore,
    AttemptRecord,
    Outcome,
    RunMeta,
    RunResults,
    TaskResult,
)
from caliper.schema.spec import EvalSpec, TaskSpec

from conftest import task_result


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
    task = task_result(*[Outcome.NOT_CHECKED] * 3)
    assert task.usable == 0
    assert task.unusable == 0
    assert task.score is None


def test_judge_errors_are_still_counted_as_noise():
    assert task_result(Outcome.JUDGE_ERROR, Outcome.PASS).unusable == 1


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
        activation_expected=[],
    )


def test_trigger_only_task_is_detected():
    assert _trigger_task().trigger_only is True


def test_trigger_only_task_reads_as_a_skip_not_an_error():
    cell = _status_cell(_trigger_task(), k=2)
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
    )
    totals = UsageTotals.from_task_results([tr])
    assert totals.unusable_attempts == 0
    assert totals.unusable_tokens == 0
    assert totals.total_tokens == 24010


# --- regressions found in review ------------------------------------------


def test_trigger_only_survives_one_timeout_among_k():
    # Keyed on "no execution verdict", not unanimity: a single timeout must not
    # flip a correct trigger probe back to 0/3 UNUSABLE.
    tr = TaskResult(
        task_id="t",
        task_name="t",
        attempts=[
            AttemptRecord(
                attempt=1, output="", duration_seconds=1.0, outcome=Outcome.NOT_CHECKED
            ),
            AttemptRecord(
                attempt=2, output="", duration_seconds=1.0, outcome=Outcome.TIMEOUT
            ),
        ],
        activation_expected=[],
    )
    assert tr.trigger_only is True
    assert "trigger only" in _status_cell(tr, k=2).plain


def test_a_task_with_a_real_verdict_is_not_trigger_only():
    tr = TaskResult(
        task_id="t",
        task_name="t",
        attempts=[
            AttemptRecord(
                attempt=1, output="", duration_seconds=1.0, outcome=Outcome.PASS
            ),
            AttemptRecord(
                attempt=2, output="", duration_seconds=1.0, outcome=Outcome.NOT_CHECKED
            ),
        ],
    )
    assert tr.trigger_only is False


class TimingOutHarness(HarnessBackend):
    @property
    def name(self) -> str:
        return "timeout"

    def run(
        self,
        task_id,
        attempt,
        prompt,
        *,
        skill_refs,
        model,
        timeout,
        isolated_home,
        extra_path=None,
        mcp_servers=None,
        forbidden_files=None,
    ) -> AttemptResult:
        return AttemptResult(
            task_id=task_id,
            attempt=attempt,
            transcript=[],
            final_output="",
            exit_code=124,
            duration_seconds=0.1,
            timed_out=True,
        )


def test_a_timeout_records_activation_as_unobserved_not_as_empty(tmp_path):
    # A truncated transcript yields no evidence either way. Recording `[]` would
    # put a fabricated "the description never fired" into the saved JSON.
    skill_dir = tmp_path / "s"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("---\nname: mine\ndescription: d\n---\nbody")
    spec_path = tmp_path / "s.eval.yaml"
    spec_path.write_text("tasks: []\n")

    results = run(
        spec=EvalSpec(
            skills=[str(skill_dir / "SKILL.md")],
            tasks=[TaskSpec(id="task-001", name="t", prompt="p", activates=["mine"])],
        ),
        spec_path=spec_path,
        harness=TimingOutHarness(),
        judge=CountingJudge(),
        k=1,
        workers=1,
        timeout=30,
    )

    attempt = results.task_results[0].attempts[0]
    assert attempt.outcome is Outcome.TIMEOUT
    assert attempt.activated is None
    assert attempt.activation_passed is None


def test_a_healthy_trigger_probe_resets_the_fail_fast_streak():
    # NOT_CHECKED is a healthy attempt; leaving it neutral would let a run abort
    # mid-way and silently truncate the activation sample.
    assert Outcome.NOT_CHECKED.is_execution_noise is False
    assert Outcome.JUDGE_ERROR.is_execution_noise is True


# --- every reader tells the same story ------------------------------------


def _trigger_only_run() -> RunResults:
    """A spec whose every task is a trigger probe: nothing execution was asked."""
    return RunResults(
        run=RunMeta(
            spec="probes",
            timestamp=datetime(2026, 7, 3, tzinfo=timezone.utc),
            k=2,
            backend="claude-code",
            era="install-and-discover",
        ),
        skill_snapshots=[],
        task_results=[_trigger_task()],
        # What the runner builds for such a run: an average over no scored task.
        aggregate=AggregateScore(avg_score=0.0, scored_tasks=0, per_task=[]),
    )


def test_an_unmeasured_run_has_no_headline() -> None:
    assert _trigger_only_run().aggregate.measured is False


def test_list_renders_an_unmeasured_run_as_skipped_not_zero(
    monkeypatch, tmp_path
) -> None:
    """The listing and the report must not disagree about the same file.

    `list` printed ``avg_score`` unconditionally, so an all-trigger-probe run —
    in which nothing failed, because nothing was asked — read as ``0.0%``: the
    exact fabricated failure ``not_checked`` exists to prevent.
    """
    results = _trigger_only_run()
    out = tmp_path / ".caliper" / "results" / "probes"
    out.mkdir(parents=True)
    (out / "2026-07-03T10-00-00Z.json").write_text(results.model_dump_json())
    monkeypatch.chdir(tmp_path)

    listed = CliRunner().invoke(app, ["list", "probes"])

    assert listed.exit_code == 0, listed.stdout
    assert "2026-07-03T10-00-00Z" in listed.stdout
    assert "0.0%" not in listed.stdout
    assert "no execution checks" in listed.stdout


def test_list_still_prints_a_measured_score() -> None:
    """The skip is keyed on `measured`, not on the number being zero.

    A run that really did score 0% must still say so.
    """
    results = _trigger_only_run()
    results.aggregate = AggregateScore(avg_score=0.0, scored_tasks=1, per_task=[])

    assert _score_cell(results) == "0.0%"


def test_an_interrupted_unmeasured_run_keeps_its_marker() -> None:
    """Both facts are provenance, and neither displaces the other.

    A run can stop early *and* have measured nothing; dropping the marker there
    left the "stopped early" legend explaining a glyph nowhere on screen.
    """
    results = _trigger_only_run()
    results.run.interrupted = True

    cell = _score_cell(results)

    assert RULE_GLYPH in cell
    assert UNUSABLE_GLYPH in cell
