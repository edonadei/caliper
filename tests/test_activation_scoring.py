from __future__ import annotations

import pytest

from caliper.schema.results import (
    AttemptRecord,
    Outcome,
    SkillActivationStats,
    TaskResult,
)
from caliper.scoring import aggregate_activation


def attempt(
    n: int,
    outcome: Outcome = Outcome.PASS,
    activated: list[str] | None = None,
    activation_passed: bool | None = None,
) -> AttemptRecord:
    return AttemptRecord(
        attempt=n,
        output="",
        duration_seconds=1.0,
        outcome=outcome,
        activated=activated,
        activation_passed=activation_passed,
    )


def task(
    name: str, attempts: list[AttemptRecord], expected: list[str] | None
) -> TaskResult:
    return TaskResult(
        task_id="task-001",
        task_name=name,
        attempts=attempts,
        successes=sum(1 for a in attempts if a.outcome == Outcome.PASS),
        unusable=sum(1 for a in attempts if not a.outcome.is_usable),
        pass_at_k=None,
        activation_expected=expected,
    )


def test_a_task_asserting_nothing_is_skipped_not_scored_zero():
    t = task("t", [attempt(1), attempt(2)], None)
    assert t.activation_score is None
    agg = aggregate_activation([t])
    assert agg.avg_score is None
    assert agg.tasks == 0


def test_exact_match_across_attempts_scores_one():
    attempts = [
        attempt(n, activated=["mine"], activation_passed=True) for n in (1, 2, 3)
    ]
    t = task("t", attempts, ["mine"])
    assert t.activation_score == 1.0
    assert aggregate_activation([t]).avg_score == 1.0


def test_judge_error_attempts_still_count_toward_activation():
    # The agent ran and the transcript is whole; only the grader broke.
    attempts = [
        attempt(1, Outcome.JUDGE_ERROR, activated=["mine"], activation_passed=True),
        attempt(2, Outcome.PASS, activated=["mine"], activation_passed=True),
    ]
    t = task("t", attempts, ["mine"])
    assert t.activation_usable == 2
    assert t.activation_score == 1.0


def test_timeouts_and_infra_errors_are_excluded_from_activation():
    # An empty observed set on a truncated transcript is a fabricated negative.
    attempts = [
        attempt(1, Outcome.TIMEOUT, activated=[], activation_passed=False),
        attempt(2, Outcome.INFRA_ERROR, activated=[], activation_passed=False),
        attempt(3, Outcome.PASS, activated=["mine"], activation_passed=True),
    ]
    t = task("t", attempts, ["mine"])
    assert t.activation_usable == 1
    assert t.activation_score == 1.0


def test_a_task_whose_every_attempt_timed_out_is_unmeasured_not_zero():
    attempts = [
        attempt(n, Outcome.TIMEOUT, activated=[], activation_passed=False)
        for n in (1, 2)
    ]
    assert task("t", attempts, ["mine"]).activation_score is None


def test_execution_and_activation_denominators_differ():
    # judge_error is unusable for execution but usable for activation.
    attempts = [
        attempt(1, Outcome.JUDGE_ERROR, activated=["mine"], activation_passed=True),
        attempt(2, Outcome.PASS, activated=["mine"], activation_passed=True),
    ]
    t = task("t", attempts, ["mine"])
    assert t.usable == 1
    assert t.activation_usable == 2


# --- per-skill recall / precision -----------------------------------------


def test_recall_counts_attempts_where_an_expected_skill_fired():
    attempts = [
        attempt(1, activated=["mine"], activation_passed=True),
        attempt(2, activated=[], activation_passed=False),
        attempt(3, activated=["mine"], activation_passed=True),
        attempt(4, activated=[], activation_passed=False),
    ]
    stats = aggregate_activation([task("t", attempts, ["mine"])]).per_skill
    mine = next(s for s in stats if s.skill == "mine")
    assert mine.expected == 4
    assert mine.hits == 2
    assert mine.recall == 0.5


def test_precision_counts_attempts_where_a_firing_skill_was_wanted():
    # Fires on its own prompt (wanted) and on a neighbour's (not wanted).
    own = task(
        "own", [attempt(1, activated=["mine"], activation_passed=True)], ["mine"]
    )
    neighbours = task(
        "neighbour",
        [
            attempt(1, activated=["other"], activation_passed=True),
            attempt(2, activated=["mine", "other"], activation_passed=False),
        ],
        ["other"],
    )
    stats = aggregate_activation([own, neighbours]).per_skill
    mine = next(s for s in stats if s.skill == "mine")
    assert mine.fired == 2
    assert mine.hits == 1
    assert mine.precision == 0.5
    assert mine.recall == 1.0


def test_per_skill_stats_ignore_unasserted_tasks():
    asserted = task(
        "a", [attempt(1, activated=["mine"], activation_passed=True)], ["mine"]
    )
    unasserted = task("b", [attempt(1, activated=["mine"])], None)
    mine = next(
        s
        for s in aggregate_activation([asserted, unasserted]).per_skill
        if s.skill == "mine"
    )
    assert mine.expected == 1
    assert mine.fired == 1


def test_a_skill_that_never_fires_has_no_precision():
    attempts = [attempt(1, activated=[], activation_passed=False)]
    mine = next(
        s
        for s in aggregate_activation([task("t", attempts, ["mine"])]).per_skill
        if s.skill == "mine"
    )
    assert mine.precision is None
    assert mine.recall == 0.0


def test_aggregate_averages_over_asserted_tasks_only():
    perfect = task(
        "a", [attempt(1, activated=["mine"], activation_passed=True)], ["mine"]
    )
    missed = task("b", [attempt(1, activated=[], activation_passed=False)], ["mine"])
    skipped = task("c", [attempt(1)], None)
    agg = aggregate_activation([perfect, missed, skipped])
    assert agg.avg_score == 0.5
    assert agg.tasks == 2


# --- restraint: the reader-facing second direction ------------------------


def test_restraint_divides_by_opportunities_not_by_firings():
    # A skill wanted once, that fired once wrongly across many silent chances.
    # precision = 1/2 = 50% (it fired twice, one was wrong).
    # restraint = 9/10 = 90% (ten attempts did not want it; it held back on 9).
    # Reporting `1 - precision` here would claim 50% restraint and read as a
    # far worse skill than it is.
    stats = SkillActivationStats(skill="mine", total=11, expected=1, fired=2, hits=1)
    assert stats.precision == 0.5
    assert stats.restraint == 0.9
    assert stats.unwanted == 1


def test_perfect_restraint_when_it_never_fires_unwanted():
    stats = SkillActivationStats(skill="mine", total=6, expected=3, fired=3, hits=3)
    assert stats.restraint == 1.0
    assert stats.recall == 1.0


def test_restraint_is_none_when_every_attempt_wanted_it():
    # No chance to hold back is not the same as failing to.
    stats = SkillActivationStats(skill="mine", total=4, expected=4, fired=4, hits=4)
    assert stats.restraint is None


def test_a_hijacker_has_full_recall_and_poor_restraint():
    # Fires on all 6 prompts that want it, and on 2 of the 3 that do not.
    stats = SkillActivationStats(skill="mine", total=9, expected=6, fired=8, hits=6)
    assert stats.recall == 1.0
    assert stats.restraint == pytest.approx(1 / 3)


def test_aggregate_gives_every_skill_the_same_denominator():
    # Both skills are in scope for every scored attempt of the run.
    own = task("own", [attempt(1, activated=["a"], activation_passed=True)], ["a"])
    theirs = task(
        "theirs", [attempt(1, activated=["a"], activation_passed=False)], ["b"]
    )
    stats = {s.skill: s for s in aggregate_activation([own, theirs]).per_skill}
    assert stats["a"].total == 2
    assert stats["b"].total == 2
    # `a` fired on the attempt that wanted `b`: one unwanted firing, no restraint.
    assert stats["a"].unwanted == 1
    assert stats["a"].restraint == 0.0
