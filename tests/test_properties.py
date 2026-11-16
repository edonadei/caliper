"""Laws of scoring, token accounting and retry merging, over generated inputs.

The example tests pin a few points; these state what must hold at every one.
See docs/adr/0001 (outcomes), docs/adr/0007 (the score), docs/adr/0006 (usage)
and docs/adr/0019 (retries).
"""

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from caliper.harness.base import AttemptResult, ConversationTurn
from caliper.harness.refusal import CliRefusal, RefusalKind
from caliper.retry import merge
from caliper.schema.results import (
    AttemptRecord,
    Outcome,
    OutcomeCounts,
    TaskResult,
    TokenUsage,
    UsageTotals,
)

# Float slack for the pass@k / pass^k brackets: 1 - (1 - p) can round below p.
EPSILON = 1e-12

NOISE = [o for o in Outcome if o.is_execution_noise]

outcomes = st.lists(st.sampled_from(Outcome), max_size=20)
token_counts = st.none() | st.integers(min_value=0, max_value=10**9)
usages = st.builds(
    TokenUsage,
    input_tokens=token_counts,
    output_tokens=token_counts,
    cache_read_tokens=token_counts,
    cache_creation_tokens=token_counts,
)
durations = st.floats(min_value=0, max_value=1e4, allow_nan=False)


def _counts(values: list[Outcome]) -> OutcomeCounts:
    return OutcomeCounts(list(values))


def _record(attempt: int, outcome: Outcome, **fields) -> AttemptRecord:
    return AttemptRecord(
        attempt=attempt, output="", duration_seconds=0.0, outcome=outcome, **fields
    )


def _task(records: list[AttemptRecord]) -> TaskResult:
    return TaskResult(task_id="task-001", task_name="One", attempts=records)


@given(outcomes)
def test_outcomes_partition_into_usable_noise_and_unchecked(values) -> None:
    counts = _counts(values)
    assert counts.usable + counts.unusable + counts.unchecked == counts.completed
    assert 0 <= counts.successes <= counts.usable
    assert counts.failed == counts.usable - counts.successes


@given(outcomes)
def test_pass_hat_k_and_pass_at_k_bracket_the_score(values) -> None:
    counts = _counts(values)
    views = (counts.score, counts.pass_at_k, counts.pass_hat_k)
    if counts.usable == 0:
        assert views == (None, None, None)
        return
    score, at_k, hat_k = views
    assert score is not None
    assert at_k is not None
    assert hat_k is not None
    assert all(0.0 <= view <= 1.0 for view in (score, at_k, hat_k))
    assert hat_k <= score + EPSILON
    assert score <= at_k + EPSILON


@given(outcomes)
def test_a_pass_never_lowers_the_score(values) -> None:
    before = _counts(values).score
    after = _counts([*values, Outcome.PASS]).score
    assert after is not None
    assert before is None or after >= before


@given(outcomes, st.lists(st.sampled_from([*NOISE, Outcome.NOT_CHECKED]), min_size=1))
def test_noise_and_trigger_probes_leave_every_score_unchanged(values, extra) -> None:
    before = _counts(values)
    after = _counts([*values, *extra])
    assert (after.score, after.pass_at_k, after.pass_hat_k) == (
        before.score,
        before.pass_at_k,
        before.pass_hat_k,
    )


@given(
    st.lists(
        st.tuples(
            st.sampled_from(
                [
                    o
                    for o in Outcome
                    if o.is_activation_usable and o is not Outcome.JUDGE_ERROR
                ]
            ),
            st.none() | st.booleans(),
            st.booleans(),
        ),
        max_size=12,
    )
)
def test_a_judge_error_leaves_the_activation_score_unchanged(attempts) -> None:
    graded = _task(
        [
            _record(i, outcome, activation_passed=passed)
            for i, (outcome, passed, _) in enumerate(attempts, start=1)
        ]
    )
    # The same attempts, with the grader broken on some of them.
    broken = _task(
        [
            _record(
                i, Outcome.JUDGE_ERROR if broke else outcome, activation_passed=passed
            )
            for i, (outcome, passed, broke) in enumerate(attempts, start=1)
        ]
    )
    assert (
        broken.activation_usable,
        broken.activation_successes,
        broken.activation_score,
    ) == (
        graded.activation_usable,
        graded.activation_successes,
        graded.activation_score,
    )


@given(usages)
def test_total_tokens_sums_the_reported_parts(usage) -> None:
    parts = [
        usage.input_tokens,
        usage.output_tokens,
        usage.cache_read_tokens,
        usage.cache_creation_tokens,
    ]
    reported = [part for part in parts if part is not None]
    assert usage.total_tokens == (sum(reported) if reported else None)


@given(st.lists(st.tuples(st.sampled_from(Outcome), durations, st.none() | usages)))
def test_run_totals_are_the_attempts_spend(attempts) -> None:
    totals = UsageTotals.from_task_results(
        [
            _task(
                [
                    AttemptRecord(
                        attempt=i,
                        output="",
                        duration_seconds=seconds,
                        outcome=outcome,
                        usage=usage,
                    )
                    for i, (outcome, seconds, usage) in enumerate(attempts, start=1)
                ]
            )
        ]
    )
    # Every attempt's spend counts toward the run, and the noise subset is the
    # noise attempts' own spend (docs/CONTEXT.md → Run usage totals).
    spent = [(o, (u.total_tokens or 0) if u else 0) for o, _, u in attempts]
    assert totals.attempts == len(attempts)
    assert totals.total_tokens == sum(tokens for _, tokens in spent)
    assert totals.unusable_tokens == sum(
        tokens for outcome, tokens in spent if outcome.is_execution_noise
    )
    assert totals.unusable_attempts == sum(o.is_execution_noise for o, _, _ in attempts)
    assert totals.usable_attempts + totals.unusable_attempts == totals.attempts


@given(
    st.lists(st.tuples(st.sampled_from(Outcome), usages), min_size=1),
    st.lists(st.tuples(st.sampled_from(NOISE), st.none() | usages), min_size=1),
)
def test_noise_spend_never_moves_tokens_per_attempt(attempts, noise) -> None:
    def per_attempt(pairs: list[tuple[Outcome, TokenUsage | None]]) -> int | None:
        records = [
            _record(i, outcome, usage=usage)
            for i, (outcome, usage) in enumerate(pairs, start=1)
        ]
        return UsageTotals.from_task_results([_task(records)]).tokens_per_attempt

    assert per_attempt([*attempts, *noise]) == per_attempt(attempts)


turns = st.builds(
    ConversationTurn,
    role=st.sampled_from(["user", "assistant", "tool"]),
    content=st.text(max_size=8),
)
invocation_results = st.builds(
    AttemptResult,
    transcript=st.lists(turns, max_size=3),
    final_output=st.text(max_size=8),
    exit_code=st.integers(min_value=-1, max_value=2),
    duration_seconds=durations,
    timed_out=st.booleans(),
    error=st.none() | st.text(max_size=4),
    resolved_model=st.none() | st.text(max_size=4),
    usage=st.none() | usages,
    cancelled=st.booleans(),
    salvaged=st.booleans(),
    refusal=st.none()
    | st.builds(
        CliRefusal, kind=st.sampled_from(RefusalKind), message=st.text(max_size=4)
    ),
)


@given(invocation_results)
def test_merging_one_invocation_returns_it(invocation) -> None:
    assert merge([invocation]) == invocation


@given(st.lists(invocation_results, min_size=2, max_size=5))
def test_merge_ends_like_the_last_invocation_and_keeps_every_turn(invocations) -> None:
    merged = merge(invocations)
    last = invocations[-1]
    ending = ("final_output", "exit_code", "error", "timed_out", "resolved_model")
    ending += ("cancelled", "salvaged", "refusal")
    assert [getattr(merged, name) for name in ending] == [
        getattr(last, name) for name in ending
    ]
    # Earlier invocations add their turns unless salvaged; the last one's turns
    # are kept either way, since it is the attempt's own ending.
    earlier = [
        turn for inv in invocations[:-1] if not inv.salvaged for turn in inv.transcript
    ]
    assert merged.transcript == [*earlier, *last.transcript]
    assert merged.duration_seconds == sum(inv.duration_seconds for inv in invocations)


@given(st.lists(invocation_results, min_size=2, max_size=5))
def test_merge_sums_each_reported_token_field(invocations) -> None:
    merged = merge(invocations)
    reported = [inv.usage for inv in invocations if inv.usage is not None]
    if not reported:
        assert merged.usage is None
        return
    assert merged.usage is not None
    for name in TokenUsage.model_fields:
        present = [getattr(u, name) for u in reported if getattr(u, name) is not None]
        assert getattr(merged.usage, name) == (sum(present) if present else None)
