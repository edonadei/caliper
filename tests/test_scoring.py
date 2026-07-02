from __future__ import annotations

from caliper.scoring import aggregate_scores, pass_at_k


def test_pass_at_k_over_usable_only() -> None:
    # 3 pass, 1 task_fail, 1 infra_error at k=5 -> usable=4, successes=3.
    agg = aggregate_scores({"t1": ("Task", 3, 4, 5)})
    score = agg.per_task[0].score
    assert score == pass_at_k(3, 4)
    # The infra attempt left the denominator: this is NOT pass_at_k(3, 5).
    assert score != pass_at_k(3, 5)


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
    assert agg.avg_pass_at_k == by_id["t2"].score == 1.0


def test_average_ignores_none_scores() -> None:
    agg = aggregate_scores(
        {
            "t1": ("A", 2, 4, 4),
            "t2": ("B", 0, 0, 4),  # excluded
        }
    )
    assert agg.avg_pass_at_k == pass_at_k(2, 4)
