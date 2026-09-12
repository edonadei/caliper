"""Everything a task's result derives from its own attempts.

These numbers used to be computed twice — once by ``score_outcomes`` on the way
in, once by ``TaskResult`` on the way out — from two different inputs, and three
of the six were then thrown away. There is one derivation now, and this is it.
"""

from __future__ import annotations

from caliper.schema.results import Outcome, TaskResult

from conftest import task_result as _task


def test_counts_split_usable_from_noise() -> None:
    # 3 pass, 1 task_fail, 1 infra_error: the infra attempt leaves every
    # denominator at once.
    task = _task(
        Outcome.PASS,
        Outcome.PASS,
        Outcome.PASS,
        Outcome.TASK_FAIL,
        Outcome.INFRA_ERROR,
    )

    assert (task.successes, task.usable, task.unusable) == (3, 4, 1)


def test_every_metric_shares_the_usable_denominator() -> None:
    task = _task(Outcome.PASS, Outcome.TASK_FAIL, Outcome.INFRA_ERROR)

    assert task.score == 0.5
    assert task.pass_at_k == 1 - 0.5**2
    assert task.pass_hat_k == 0.5**2


def test_a_cheat_is_a_usable_failure() -> None:
    """It got a fair shot: it stays in the denominator as a non-success."""
    task = _task(Outcome.PASS, Outcome.CHEAT)

    assert (task.successes, task.usable, task.unusable) == (1, 2, 0)
    assert task.score == 0.5


def test_a_trigger_probe_leaves_the_denominator_without_being_noise() -> None:
    """``NOT_CHECKED`` is neither usable nor an error.

    A correct ``activates:``-only spec authored no execution check, so there was
    nothing to grade — it must not read as something having gone wrong.
    """
    task = _task(Outcome.NOT_CHECKED, Outcome.NOT_CHECKED)

    assert (task.successes, task.usable, task.unusable) == (0, 0, 0)
    assert task.score is None


def test_all_unusable_yields_no_metrics() -> None:
    task = _task(Outcome.TIMEOUT, Outcome.JUDGE_ERROR)

    assert (task.successes, task.usable, task.unusable) == (0, 0, 2)
    assert task.score is None
    assert task.pass_at_k is None
    assert task.pass_hat_k is None


def test_a_task_with_no_attempts_has_no_metrics() -> None:
    task = _task()

    assert (task.successes, task.usable, task.unusable) == (0, 0, 0)
    assert task.score is None


def test_the_metrics_are_serialized_so_a_saved_run_reads_the_same() -> None:
    """Derived, but still written: a reader of the JSON needs the numbers."""
    dumped = _task(Outcome.PASS, Outcome.TASK_FAIL).model_dump()

    assert dumped["successes"] == 1
    assert dumped["usable"] == 2
    assert dumped["unusable"] == 0
    assert dumped["score"] == 0.5
    assert dumped["pass_at_k"] == 0.75
    assert dumped["pass_hat_k"] == 0.25


def test_a_saved_run_cannot_carry_counts_that_contradict_its_attempts() -> None:
    """The stored numbers are ignored on load; the attempts are the record.

    Before, ``successes`` and ``pass_at_k`` were stored while ``score`` was
    derived, so a hand-edited or older file could report a rate that disagreed
    with the attempts printed beside it.
    """
    honest = _task(Outcome.PASS, Outcome.TASK_FAIL)
    tampered = honest.model_dump()
    tampered["successes"] = 99
    tampered["score"] = 1.0

    reloaded = TaskResult.model_validate(tampered)

    assert reloaded.successes == 1
    assert reloaded.score == 0.5


# --- read-side facts -------------------------------------------------------
#
# These used to be derived in the reporter, where only the run report could see
# them — which is how `list` came to print 0.0% for a run the report renders as
# "no execution checks". They are facts about a task result, so they are tested
# as facts, without a renderer in the way.


def test_a_task_with_no_execution_check_is_trigger_only() -> None:
    assert _task(Outcome.NOT_CHECKED, Outcome.NOT_CHECKED).trigger_only is True


def test_trigger_only_survives_one_timeout_among_k() -> None:
    """Keyed on the absence of a verdict, not on unanimity.

    A single timeout must not flip a correct trigger probe back to "0/2".
    """
    assert _task(Outcome.NOT_CHECKED, Outcome.TIMEOUT).trigger_only is True


def test_a_task_with_a_real_verdict_is_not_trigger_only() -> None:
    assert _task(Outcome.PASS, Outcome.NOT_CHECKED).trigger_only is False


def test_a_task_with_no_attempts_at_all_is_not_trigger_only() -> None:
    """It asked nothing because it ran nothing, which is the aborted case."""
    assert _task().trigger_only is False


def test_any_cheat_is_true_when_one_attempt_cheated() -> None:
    assert _task(Outcome.PASS, Outcome.CHEAT).any_cheat is True
    assert _task(Outcome.PASS, Outcome.TASK_FAIL).any_cheat is False


def test_a_task_that_ran_short_with_nothing_measured_is_aborted() -> None:
    assert _task(Outcome.INFRA_ERROR).aborted(k=3) is True


def test_a_task_that_ran_short_but_measured_something_is_not_aborted() -> None:
    """It has a rate, so "0 of 3" would be the wrong thing to say about it."""
    assert _task(Outcome.PASS).aborted(k=3) is False


def test_a_complete_task_is_never_aborted() -> None:
    assert _task(Outcome.INFRA_ERROR, Outcome.INFRA_ERROR).aborted(k=2) is False


def test_a_trigger_probe_is_never_aborted() -> None:
    """Its score is None by construction, not by running short."""
    assert _task(Outcome.NOT_CHECKED).aborted(k=3) is False


def test_a_task_rolls_up_its_own_usage() -> None:
    task = _task(Outcome.PASS, Outcome.INFRA_ERROR)

    assert task.usage.attempts == 2
    assert task.usage.unusable_attempts == 1
