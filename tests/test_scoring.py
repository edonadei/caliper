"""The metric formulas, and the run-level roll-up over many tasks.

One task's own numbers are :class:`TaskResult`'s and are tested in
``tests/test_task_metrics.py``; this file covers the formulas themselves and
what ``AggregateScore.from_task_results`` does across tasks.
"""

from __future__ import annotations

from caliper.schema.results import (
    AggregateScore,
    AttemptRecord,
    Outcome,
    TaskResult,
    pass_at_k,
    pass_hat_k,
    success_rate,
)

from conftest import task_result


def _task(task_id: str, name: str, *outcomes: Outcome) -> TaskResult:
    return task_result(*outcomes, task_id=task_id, name=name)


# --- the formulas ------------------------------------------------------------


def test_pass_at_k_and_pass_hat_k_are_secondary_views() -> None:
    # pass@k = P(>=1 of k pass); pass^k = P(all k pass), at the observed rate.
    assert pass_at_k(1, 3) == 1 - (1 - 1 / 3) ** 3  # ~0.704, retry-optimistic
    assert pass_hat_k(1, 3) == (1 / 3) ** 3  # ~0.037, strict
    assert pass_hat_k(3, 3) == 1.0
    assert pass_at_k(0, 3) == 0.0


def test_no_usable_attempt_yields_no_metric_at_all() -> None:
    """Uniformly ``None``, never ``0.0`` — a task never measured has no rate."""
    assert success_rate(0, 0) is None
    assert pass_at_k(0, 0) is None
    assert pass_hat_k(0, 0) is None


# --- the run-level roll-up ---------------------------------------------------


def test_score_is_the_raw_rate_over_usable_only() -> None:
    # 3 pass, 1 task_fail, 1 infra_error -> usable=4, successes=3.
    agg = AggregateScore.from_task_results(
        [
            _task(
                "t1",
                "Task",
                Outcome.PASS,
                Outcome.PASS,
                Outcome.PASS,
                Outcome.TASK_FAIL,
                Outcome.INFRA_ERROR,
            )
        ],
    )

    # The infra attempt left the denominator: the rate is over 4, not 5.
    assert agg.avg_score == success_rate(3, 4) == 0.75


def test_a_fully_unusable_task_scores_none_and_leaves_the_average_alone() -> None:
    agg = AggregateScore.from_task_results(
        [
            _task("t1", "Throttled", Outcome.INFRA_ERROR, Outcome.TIMEOUT),
            _task("t2", "Clean", Outcome.PASS, Outcome.PASS),
        ],
    )

    # The unmeasured task must not drag the average toward 0.
    assert agg.avg_score == 1.0
    assert agg.scored_tasks == 1


def test_average_ignores_none_scores() -> None:
    agg = AggregateScore.from_task_results(
        [
            _task("t1", "A", Outcome.PASS, Outcome.TASK_FAIL),
            _task("t2", "B", Outcome.JUDGE_ERROR),  # excluded
        ],
    )

    assert agg.avg_score == success_rate(1, 2) == 0.5


def test_no_tasks_averages_to_zero_rather_than_none() -> None:
    """An empty run is 0.0%, which is what `list` renders; it is never saved."""
    agg = AggregateScore.from_task_results([])

    assert agg.avg_score == 0.0
    assert agg.scored_tasks == 0


# --- one constructor, both scoreboards ---------------------------------------
#
# The run's numbers used to be assembled in two steps and copied across by the
# runner: `aggregate_scores` built the model, `aggregate_activation` returned a
# separate dataclass, and four fields were moved over by hand. A field added to
# one side and forgotten on the other was a silent gap. There is one
# constructor now, and this is it.


def _asserting(
    *pairs: tuple[Outcome, list[str]], expected: list[str] | None
) -> TaskResult:
    """A task that asserted `activates:` and observed something on each attempt."""
    return TaskResult(
        task_id="t1",
        task_name="One",
        attempts=[
            AttemptRecord(
                attempt=i,
                output="",
                duration_seconds=0.0,
                outcome=outcome,
                activated=activated,
                activation_passed=set(activated) == set(expected or []),
            )
            for i, (outcome, activated) in enumerate(pairs, start=1)
        ],
        activation_expected=expected,
    )


def test_one_call_builds_both_scoreboards() -> None:
    agg = AggregateScore.from_task_results(
        [_asserting((Outcome.PASS, ["a"]), (Outcome.TASK_FAIL, ["a"]), expected=["a"])],
        declared=["a"],
    )

    assert agg.avg_score == 0.5
    assert agg.avg_activation_score == 1.0
    assert agg.activation_asserted == 1
    assert [s.skill for s in agg.activation_per_skill] == ["a"]


def test_a_firing_description_over_a_failing_body_scores_one_and_zero() -> None:
    """The two scoreboards have opposite fixes, so neither may move the other.

    The skill was reached for exactly as claimed and then did the task wrong:
    activation is perfect, execution is nothing. A single headline mixing them
    would point at neither (docs/adr/0014).
    """
    agg = AggregateScore.from_task_results(
        [_asserting((Outcome.TASK_FAIL, ["mine"]), expected=["mine"])],
        declared=["mine"],
    )

    assert agg.avg_score == 0.0
    assert agg.avg_activation_score == 1.0


def test_a_passing_body_under_a_silent_description_scores_one_and_zero() -> None:
    """The mirror case: the body works, but only because the probe got lucky.

    Nothing fired where the task said it would, so the `description` is the
    suspect even though every attempt passed.
    """
    agg = AggregateScore.from_task_results(
        [_asserting((Outcome.PASS, []), expected=["mine"])], declared=["mine"]
    )

    assert agg.avg_score == 1.0
    assert agg.avg_activation_score == 0.0


def test_an_unasserted_run_has_no_activation_headline() -> None:
    """None, never 0.0: nothing was claimed, so nothing was missed."""
    agg = AggregateScore.from_task_results(
        [task_result(Outcome.PASS, task_id="t1")], declared=["a"]
    )

    assert agg.avg_activation_score is None
    assert agg.activation_asserted == 0


def test_a_declared_skill_gets_a_row_even_when_it_never_fired() -> None:
    agg = AggregateScore.from_task_results(
        [_asserting((Outcome.PASS, []), expected=[])], declared=["dormant"]
    )

    assert [s.skill for s in agg.activation_per_skill] == ["dormant"]
