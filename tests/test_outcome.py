from __future__ import annotations

from caliper.harness.base import AttemptResult, ConversationTurn
from caliper.harness.refusal import CliRefusal, RefusalKind
from caliper.judge.base import JudgeResult
from caliper.outcome import classify_pre_judge, judge_outcome
from caliper.schema.results import Outcome, TokenUsage


def _harness(
    *,
    exit_code: int = 0,
    error: str | None = None,
    timed_out: bool = False,
    final_output: str = "ok",
    transcript: list[ConversationTurn] | None = None,
    salvaged: bool = False,
    usage: TokenUsage | None = None,
    refusal: CliRefusal | None = None,
) -> AttemptResult:
    return AttemptResult(
        transcript=(
            [ConversationTurn(role="assistant", content=final_output)]
            if transcript is None
            else transcript
        ),
        final_output=final_output,
        exit_code=exit_code,
        duration_seconds=1.0,
        error=error,
        timed_out=timed_out,
        salvaged=salvaged,
        usage=usage,
        refusal=refusal,
    )


def _judge(*, passed: bool, errored: bool = False) -> JudgeResult:
    return JudgeResult(passed=passed, reasoning="r", errored=errored)


# --- judge_outcome: the verdict's half of the label ------------------------
#
# Precedence between the harness result, cheat detection and the judge lives in
# ``assemble_attempt`` (tests/test_attempt.py); this only labels a verdict.


def test_judge_pass() -> None:
    assert judge_outcome(_judge(passed=True)) is Outcome.PASS


def test_judge_task_fail() -> None:
    assert judge_outcome(_judge(passed=False)) is Outcome.TASK_FAIL


def test_judge_error_when_no_verdict_survived() -> None:
    assert judge_outcome(_judge(passed=False, errored=True)) is Outcome.JUDGE_ERROR


# --- classify_pre_judge: the skip predicate the runner shares -------------


def test_pre_judge_none_on_clean_attempt() -> None:
    # Ran cleanly: no skip, proceed to cheat detection and judging.
    assert classify_pre_judge(_harness()) is None


def test_pre_judge_timeout() -> None:
    assert classify_pre_judge(_harness(timed_out=True)).outcome is Outcome.TIMEOUT


def test_pre_judge_infra_on_nonzero_exit() -> None:
    assert classify_pre_judge(_harness(exit_code=1)).outcome is Outcome.INFRA_ERROR


def test_pre_judge_infra_on_a_refusal_despite_zero_exit() -> None:
    h = _harness(
        exit_code=0,
        final_output="Spending cap reached resets 4:30am",
        salvaged=True,
        usage=TokenUsage(input_tokens=12),
        refusal=CliRefusal(RefusalKind.THROTTLE, "429 rate limit"),
    )
    exit = classify_pre_judge(h)
    assert exit.outcome is Outcome.INFRA_ERROR
    assert exit.evidence == "429 rate limit"


def test_pre_judge_keeps_the_refusal_as_evidence_when_no_model_call_was_seen() -> None:
    h = _harness(
        transcript=[],
        final_output="",
        refusal=CliRefusal(RefusalKind.THROTTLE, "429 rate limit; retry at 5pm"),
    )
    assert classify_pre_judge(h).evidence == "429 rate limit; retry at 5pm"


def test_pre_judge_ignores_an_answer_that_mentions_a_limit() -> None:
    # The harness found no refusal in what the CLI wrote, so the words are the
    # agent's own (docs/adr/0030).
    h = _harness(final_output="I added handling for the 429 rate limit.")
    assert classify_pre_judge(h) is None


def test_pre_judge_infra_when_no_model_call_was_observed() -> None:
    # Zero exit, nothing parsed, no tokens: the CLI bailed before any model
    # call (an expired login reported inside its own stream). Nothing to judge.
    for h in (
        _harness(transcript=[], final_output=""),
        _harness(
            transcript=[],
            final_output="",
            usage=TokenUsage(input_tokens=0, output_tokens=0),
        ),
        _harness(salvaged=True, final_output='{"type":"agent_end"}'),
        # A session export can carry the prompt it was given and nothing the
        # model said: the input alone is no evidence of a call.
        _harness(
            transcript=[ConversationTurn(role="user", content="Do it")],
            final_output="",
        ),
    ):
        assert classify_pre_judge(h).outcome is Outcome.INFRA_ERROR


def test_pre_judge_none_when_an_unparsed_attempt_still_spent_tokens() -> None:
    # The model was called; only the parser missed the stream. The salvaged
    # stdout is still the agent's answer, so it goes to the judge.
    h = _harness(salvaged=True, usage=TokenUsage(input_tokens=10, output_tokens=5))
    assert classify_pre_judge(h) is None


def test_pre_judge_none_for_an_answer_with_no_transcript_or_usage() -> None:
    # A backend may hand back only its parsed answer. Unlike salvaged stdout,
    # that is the agent speaking, so it goes to the judge.
    assert classify_pre_judge(_harness(transcript=[])) is None


def test_pre_judge_ignores_cheat_and_judge_states() -> None:
    # Cheat is not a pre-judge concern: it needs the transcript scan that runs
    # after this predicate, so a clean-exit attempt returns None here.
    assert classify_pre_judge(_harness()) is None
