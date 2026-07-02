from __future__ import annotations

import pytest

from caliper.judge.base import JudgeResult
from caliper.schema.results import AttemptRecord, Outcome
from caliper.scoring import classify_outcome, pass_at_k, score_outcomes


def test_classify_outcome_covers_attempt_taxonomy() -> None:
    assert (
        classify_outcome(
            exit_code=0,
            error=None,
            cheated=False,
            judge_result=JudgeResult(passed=True, reasoning="ok"),
        )
        == Outcome.PASS
    )
    assert (
        classify_outcome(
            exit_code=0,
            error=None,
            cheated=False,
            judge_result=JudgeResult(passed=False, reasoning="wrong answer"),
        )
        == Outcome.TASK_FAIL
    )
    assert (
        classify_outcome(
            exit_code=1,
            error="Spending cap reached resets 4:30am",
            cheated=False,
            judge_result=None,
        )
        == Outcome.INFRA_ERROR
    )
    assert (
        classify_outcome(
            exit_code=124,
            error="timeout",
            cheated=False,
            judge_result=None,
        )
        == Outcome.TIMEOUT
    )
    assert (
        classify_outcome(
            exit_code=0,
            error=None,
            cheated=True,
            judge_result=None,
        )
        == Outcome.CHEAT
    )
    assert (
        classify_outcome(
            exit_code=0,
            error=None,
            cheated=False,
            judge_result=JudgeResult(
                passed=False,
                reasoning="Judge returned unparseable response: nope",
                autorater_passed=False,
                autorater_reasoning="Judge returned unparseable response: nope",
            ),
        )
        == Outcome.JUDGE_ERROR
    )


def test_score_outcomes_excludes_unusable_attempts_from_pass_at_k() -> None:
    score = score_outcomes(
        [
            Outcome.PASS,
            Outcome.TASK_FAIL,
            Outcome.INFRA_ERROR,
            Outcome.TIMEOUT,
            Outcome.JUDGE_ERROR,
            Outcome.CHEAT,
        ]
    )

    assert score.successes == 1
    assert score.usable_attempts == 3
    assert score.unusable_attempts == 3
    assert score.pass_at_k == pytest.approx(pass_at_k(successes=1, k=3))


def test_attempt_record_defaults_outcome_for_old_json() -> None:
    assert (
        AttemptRecord(
            attempt=1,
            output="ok",
            duration_seconds=0.1,
            passed=True,
        ).outcome
        == Outcome.PASS
    )
    assert (
        AttemptRecord(
            attempt=1,
            output="bad",
            duration_seconds=0.1,
            passed=False,
            cheated=True,
        ).outcome
        == Outcome.CHEAT
    )


def test_attempt_record_keeps_passed_equal_to_outcome() -> None:
    record = AttemptRecord(
        attempt=1,
        output="rate limited",
        duration_seconds=0.1,
        passed=True,
        outcome=Outcome.INFRA_ERROR,
    )

    assert record.passed is False
