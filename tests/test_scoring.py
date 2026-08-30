"""The metric formulas, and the run-level roll-up over many tasks.

One task's own numbers are :class:`TaskResult`'s and are tested in
``tests/test_task_metrics.py``; this file covers the formulas themselves and
what ``aggregate_scores`` does across tasks.
"""

from __future__ import annotations

from caliper.schema.results import (
    Outcome,
    TaskResult,
    pass_at_k,
    pass_hat_k,
    success_rate,
)
from caliper.scoring import aggregate_scores

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
    agg = aggregate_scores(
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
        k=5,
    )

    # The infra attempt left the denominator: the rate is over 4, not 5.
    assert agg.per_task[0].score == success_rate(3, 4) == 0.75


def test_a_fully_unusable_task_scores_none_and_leaves_the_average_alone() -> None:
    agg = aggregate_scores(
        [
            _task("t1", "Throttled", Outcome.INFRA_ERROR, Outcome.TIMEOUT),
            _task("t2", "Clean", Outcome.PASS, Outcome.PASS),
        ],
        k=2,
    )

    by_id = {t.task_id: t for t in agg.per_task}
    assert by_id["t1"].score is None
    # The unmeasured task must not drag the average toward 0.
    assert agg.avg_score == by_id["t2"].score == 1.0
    assert agg.scored_tasks == 1


def test_average_ignores_none_scores() -> None:
    agg = aggregate_scores(
        [
            _task("t1", "A", Outcome.PASS, Outcome.TASK_FAIL),
            _task("t2", "B", Outcome.JUDGE_ERROR),  # excluded
        ],
        k=2,
    )

    assert agg.avg_score == success_rate(1, 2) == 0.5


def test_every_row_records_the_runs_requested_depth() -> None:
    """A task that ran short of k is visible as such, not silently rescaled."""
    agg = aggregate_scores([_task("t1", "Cut short", Outcome.PASS)], k=5)

    assert agg.per_task[0].k == 5
    assert agg.per_task[0].successes == 1


def test_no_tasks_averages_to_zero_rather_than_none() -> None:
    """An empty run is 0.0%, which is what `list` renders; it is never saved."""
    agg = aggregate_scores([], k=3)

    assert agg.avg_score == 0.0
    assert agg.per_task == []
