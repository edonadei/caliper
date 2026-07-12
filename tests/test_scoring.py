from __future__ import annotations

from caliper.schema.results import Outcome
from caliper.scoring import (
    aggregate_scores,
    pass_at_k,
    pass_hat_k,
    score_outcomes,
    success_rate,
)


def test_score_is_raw_rate_over_usable_only() -> None:
    # 3 pass, 1 task_fail, 1 infra_error at k=5 -> usable=4, successes=3.
    agg = aggregate_scores({"t1": ("Task", 3, 4, 5)})
    score = agg.per_task[0].score
    # The primary score is the raw success rate over usable attempts.
    assert score == success_rate(3, 4) == 0.75
    # The infra attempt left the denominator: rate is over 4 usable, not 5.
    assert score != success_rate(3, 5)


def test_all_unusable_scores_none_and_is_excluded_from_average() -> None:
    agg = aggregate_scores(
        {
            "t1": ("Throttled", 0, 0, 5),  # every attempt unusable
            "t2": ("Clean", 5, 5, 5),  # perfect
        }
    )
    by_id = {t.task_id: t for t in agg.per_task}
    assert by_id["t1"].score is None
    # The fully-unusable task must not drag the average toward 0.
    assert agg.avg_score == by_id["t2"].score == 1.0


def test_average_ignores_none_scores() -> None:
    agg = aggregate_scores(
        {
            "t1": ("A", 2, 4, 4),
            "t2": ("B", 0, 0, 4),  # excluded
        }
    )
    assert agg.avg_score == success_rate(2, 4) == 0.5


def test_pass_at_k_and_pass_hat_k_are_secondary_views() -> None:
    # pass@k = P(>=1 of k pass); pass^k = P(all k pass), at the observed rate.
    assert pass_at_k(1, 3) == 1 - (1 - 1 / 3) ** 3  # ~0.704, retry-optimistic
    assert pass_hat_k(1, 3) == (1 / 3) ** 3  # ~0.037, strict
    assert pass_hat_k(3, 3) == 1.0
    assert pass_at_k(0, 3) == 0.0
    # No usable attempt -> no metric, uniformly across all three views.
    assert pass_at_k(0, 0) is None
    assert pass_hat_k(0, 0) is None
    assert success_rate(0, 0) is None


def test_score_outcomes_uses_usable_denominator_everywhere() -> None:
    # 3 pass, 1 task_fail, 1 infra_error: usable=4 — the infra attempt leaves
    # every denominator at once.
    scores = score_outcomes(
        [
            Outcome.PASS,
            Outcome.PASS,
            Outcome.PASS,
            Outcome.TASK_FAIL,
            Outcome.INFRA_ERROR,
        ]
    )

    assert (scores.successes, scores.usable, scores.unusable) == (3, 4, 1)
    assert scores.score == success_rate(3, 4) == 0.75
    assert scores.pass_at_k == pass_at_k(3, 4)
    assert scores.pass_hat_k == pass_hat_k(3, 4)


def test_score_outcomes_cheat_counts_as_usable_failure() -> None:
    # A cheat got a fair shot: it stays in the denominator as a non-success.
    scores = score_outcomes([Outcome.PASS, Outcome.CHEAT])
    assert (scores.successes, scores.usable, scores.unusable) == (1, 2, 0)
    assert scores.score == 0.5


def test_score_outcomes_all_unusable_yields_no_metrics() -> None:
    scores = score_outcomes([Outcome.TIMEOUT, Outcome.JUDGE_ERROR])
    assert (scores.successes, scores.usable, scores.unusable) == (0, 0, 2)
    assert scores.score is None
    assert scores.pass_at_k is None
    assert scores.pass_hat_k is None


def test_score_outcomes_empty() -> None:
    scores = score_outcomes([])
    assert (scores.successes, scores.usable, scores.unusable) == (0, 0, 0)
    assert scores.score is None
