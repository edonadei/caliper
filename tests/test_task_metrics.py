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
