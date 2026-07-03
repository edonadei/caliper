from __future__ import annotations

from caliper.harness.base import AttemptResult
from caliper.judge.base import JudgeResult
from caliper.outcome import (
    classify_outcome,
    classify_pre_judge,
    looks_like_infra_failure,
)
from caliper.schema.results import Outcome


def _harness(
    *,
    exit_code: int = 0,
    error: str | None = None,
    timed_out: bool = False,
    final_output: str = "ok",
) -> AttemptResult:
    return AttemptResult(
        task_id="t",
        attempt=1,
        transcript=[],
        final_output=final_output,
        exit_code=exit_code,
        duration_seconds=1.0,
        error=error,
        timed_out=timed_out,
    )


def _judge(*, passed: bool, errored: bool = False) -> JudgeResult:
    return JudgeResult(passed=passed, reasoning="r", errored=errored)


# --- classify_outcome: one assertion per branch ---------------------------


def test_classify_pass() -> None:
    assert classify_outcome(_harness(), [], _judge(passed=True)) is Outcome.PASS


def test_classify_task_fail() -> None:
    assert classify_outcome(_harness(), [], _judge(passed=False)) is Outcome.TASK_FAIL


def test_classify_judge_error() -> None:
    out = classify_outcome(_harness(), [], _judge(passed=False, errored=True))
    assert out is Outcome.JUDGE_ERROR


def test_classify_judge_error_when_judge_missing() -> None:
    assert classify_outcome(_harness(), [], None) is Outcome.JUDGE_ERROR


def test_classify_infra_error_on_nonzero_exit() -> None:
    out = classify_outcome(_harness(exit_code=1, error="boom"), [], None)
    assert out is Outcome.INFRA_ERROR


def test_classify_infra_error_on_rate_limit_signal_despite_zero_exit() -> None:
    # The motivating incident: a spending cap that exits 0 with the cap as output.
    h = _harness(exit_code=0, final_output="Spending cap reached resets 4:30am")
    assert classify_outcome(h, [], _judge(passed=False)) is Outcome.INFRA_ERROR


def test_classify_timeout() -> None:
    out = classify_outcome(
        _harness(exit_code=124, error="timeout", timed_out=True), [], None
    )
    assert out is Outcome.TIMEOUT


def test_classify_cheat() -> None:
    assert (
        classify_outcome(_harness(), ["/forbidden/answers.txt"], None) is Outcome.CHEAT
    )


def test_precedence_timeout_beats_infra() -> None:
    # A timed-out attempt also has a nonzero exit; timeout must win.
    out = classify_outcome(_harness(exit_code=124, timed_out=True), [], None)
    assert out is Outcome.TIMEOUT


def test_precedence_infra_beats_cheat_and_judge() -> None:
    h = _harness(exit_code=1)
    assert classify_outcome(h, ["/x"], _judge(passed=True)) is Outcome.INFRA_ERROR


# --- classify_pre_judge: the skip predicate the runner shares -------------


def test_pre_judge_none_on_clean_attempt() -> None:
    # Ran cleanly: no skip, proceed to cheat detection and judging.
    assert classify_pre_judge(_harness()) is None


def test_pre_judge_timeout() -> None:
    assert classify_pre_judge(_harness(timed_out=True)) is Outcome.TIMEOUT


def test_pre_judge_infra_on_nonzero_exit() -> None:
    assert classify_pre_judge(_harness(exit_code=1)) is Outcome.INFRA_ERROR


def test_pre_judge_infra_on_signal_despite_zero_exit() -> None:
    h = _harness(exit_code=0, final_output="Spending cap reached resets 4:30am")
    assert classify_pre_judge(h) is Outcome.INFRA_ERROR


def test_pre_judge_ignores_cheat_and_judge_states() -> None:
    # Cheat is not a pre-judge concern: it needs the transcript scan that runs
    # after this predicate, so a clean-exit attempt returns None here.
    assert classify_pre_judge(_harness()) is None


def test_classify_outcome_reuses_pre_judge_predicate() -> None:
    # The final label agrees with the skip decision on every early-exit path.
    for h in (_harness(timed_out=True), _harness(exit_code=1)):
        assert classify_outcome(h, [], None) is classify_pre_judge(h)


# --- looks_like_infra_failure --------------------------------------------


def test_looks_like_infra_matches_known_signals() -> None:
    for text in (
        "Spending cap reached",
        "rate limit exceeded",
        "HTTP 429 Too Many Requests",
        "the model is overloaded",
        "quota exceeded for this key",
    ):
        assert looks_like_infra_failure(text), text


def test_looks_like_infra_ignores_normal_output() -> None:
    assert not looks_like_infra_failure("The assistant wrote the file successfully.")
    assert not looks_like_infra_failure("")
