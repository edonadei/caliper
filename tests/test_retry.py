"""A throttled invocation is retried; a spending cap stops the run.

The question every case here asks is the one that splits `INFRA_ERROR` in three:
would another invocation behave differently? A 429 yes, a cap no, a crash no.
See docs/adr/0019-an-attempt-may-be-invoked-more-than-once.md.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from caliper import cancel
from caliper.harness.base import AttemptResult, ConversationTurn, HarnessBackend
from caliper.judge.base import JudgeResult
from caliper.retry import (
    RetryPolicy,
    SpendingCapReached,
    invoke_with_retry,
    merge,
)
from caliper.runner import RunAborted, run
from caliper.schema.results import Outcome, TokenUsage
from caliper.schema.spec import EvalSpec, TaskSpec

# No real waiting anywhere in this file: the policy's timing is asserted
# directly, and every loop test injects zero delays.
NO_WAIT = RetryPolicy(base_delay_seconds=0.0, jitter=0.0)


def _result(
    *,
    output: str = "done",
    error: str | None = None,
    exit_code: int = 0,
    duration: float = 1.0,
    usage: TokenUsage | None = None,
    timed_out: bool = False,
    salvaged: bool = False,
) -> AttemptResult:
    """One invocation's result.

    ``salvaged`` models the shape that matters to the retry seam: nothing parsed
    out of the agent's stream, so the raw stdout was rescued as a single turn.
    That is what a CLI bailing looks like, as against an agent answering — see
    ``caliper.outcome.answered``.
    """
    return AttemptResult(
        task_id="task-001",
        attempt=1,
        transcript=[ConversationTurn(role="assistant", content=output)],
        final_output=output,
        exit_code=exit_code,
        duration_seconds=duration,
        error=error,
        usage=usage,
        timed_out=timed_out,
        salvaged=salvaged,
    )


# A throttled CLI exits non-zero with the refusal on stderr and nothing parsed.
THROTTLED = dict(
    output="", error="Error 429: rate limit exceeded", exit_code=1, salvaged=True
)
# A capped CLI exits *zero* with the cap message as its only output — the case
# that forces signals to be matched on a clean exit at all.
CAPPED = dict(output="You have reached your usage limit for this month.", salvaged=True)


def _queue(*results: AttemptResult):
    """An `invoke` that hands back each result in turn."""
    remaining = list(results)
    calls: list[int] = []

    def invoke() -> AttemptResult:
        calls.append(len(calls) + 1)
        return remaining.pop(0)

    return invoke, calls


def test_a_throttled_invocation_is_retried_until_it_lands() -> None:
    invoke, calls = _queue(_result(**THROTTLED), _result(**THROTTLED), _result())

    invoked = invoke_with_retry(invoke, NO_WAIT)

    assert len(calls) == 3
    assert invoked.retries == 2
    assert invoked.result.final_output == "done"


def test_retries_are_bounded() -> None:
    invoke, calls = _queue(*[_result(**THROTTLED) for _ in range(5)])

    invoked = invoke_with_retry(invoke, NO_WAIT)

    assert len(calls) == 3  # the first invocation plus max_retries
    assert invoked.retries == 2
    # Still handed back as a normal (unusable) result, not an exception: a
    # throttle that never clears is one dead attempt, not a dead run.
    assert invoked.result.exit_code == 1


def test_a_clean_invocation_is_never_retried() -> None:
    invoke, calls = _queue(_result())

    invoked = invoke_with_retry(invoke, NO_WAIT)

    assert len(calls) == 1
    assert invoked.retries == 0


def test_a_timeout_is_not_retried() -> None:
    """Nothing says the next spawn would be faster."""
    invoke, calls = _queue(_result(output="", error="timeout", timed_out=True))

    invoke_with_retry(invoke, NO_WAIT)

    assert len(calls) == 1


def test_a_bare_crash_is_not_retried() -> None:
    """Retrying a reproducible crash hides a defect instead of surviving one."""
    invoke, calls = _queue(_result(output="", error="Traceback: boom", exit_code=1))

    invoke_with_retry(invoke, NO_WAIT)

    assert len(calls) == 1


def test_a_spending_cap_raises_instead_of_retrying() -> None:
    invoke, calls = _queue(_result(**CAPPED), _result())

    with pytest.raises(SpendingCapReached) as excinfo:
        invoke_with_retry(invoke, NO_WAIT)

    assert len(calls) == 1
    assert "usage limit" in str(excinfo.value)


def test_a_cancelled_backoff_stops_retrying() -> None:
    """Ctrl-C during a backoff must not sit out the wait for a doomed attempt."""
    cancel.request()
    invoke, calls = _queue(_result(**THROTTLED), _result())

    invoked = invoke_with_retry(invoke, RetryPolicy(base_delay_seconds=30.0))

    assert len(calls) == 1
    assert invoked.retries == 0


def test_backoff_grows_and_is_jittered() -> None:
    policy = RetryPolicy(base_delay_seconds=2.0, jitter=0.25)

    first = [policy.delay_for(0) for _ in range(50)]
    second = [policy.delay_for(1) for _ in range(50)]

    assert all(2.0 <= d <= 2.5 for d in first)
    assert all(4.0 <= d <= 5.0 for d in second)
    # Jittered, so parallel workers throttled together do not return in lockstep.
    assert len(set(first)) > 1


def test_merge_keeps_the_last_invocations_answer() -> None:
    merged = merge([_result(output="429 rate limit"), _result(output="the answer")])

    assert merged.final_output == "the answer"
    assert merged.transcript[-1].content == "the answer"


def test_merge_sums_cost_and_spawn_time_but_not_the_waiting() -> None:
    merged = merge(
        [
            _result(duration=3.0, usage=TokenUsage(input_tokens=10, output_tokens=1)),
            _result(duration=5.0, usage=TokenUsage(input_tokens=20, output_tokens=2)),
        ]
    )

    # Both spawns really happened, and both spent tokens.
    assert merged.duration_seconds == 8.0
    assert merged.usage.input_tokens == 30
    assert merged.usage.output_tokens == 3


def test_merge_leaves_usage_none_when_no_invocation_reported_any() -> None:
    """A backend that reports nothing must not become a confident zero."""
    merged = merge([_result(usage=None), _result(usage=None)])

    assert merged.usage is None


class ThrottleThenPassHarness(HarnessBackend):
    """Throttles the first invocation of every attempt, then answers."""

    def __init__(self) -> None:
        self.invocations = 0

    @property
    def name(self) -> str:
        return "throttling"

    def run(self, task_id: str, attempt: int, prompt: str, **kwargs) -> AttemptResult:
        self.invocations += 1
        if self.invocations % 2 == 1:
            return _result(**THROTTLED)
        return _result()


class CappedHarness(HarnessBackend):
    @property
    def name(self) -> str:
        return "capped"

    def run(self, task_id: str, attempt: int, prompt: str, **kwargs) -> AttemptResult:
        return _result(**CAPPED)


class PassingJudge:
    def evaluate(self, task, transcript, final_output, spec_dir) -> JudgeResult:
        return JudgeResult(passed=True, reasoning="ok")


def _spec() -> EvalSpec:
    return EvalSpec(
        tasks=[
            TaskSpec(
                id="task-001",
                name="One",
                prompt="Do the thing",
                assert_script="assert True",
            )
        ]
    )


def _spec_file(tmp_path: Path) -> Path:
    path = tmp_path / "sample.eval.yaml"
    path.write_text("tasks: []\n")
    return path


def test_a_retried_attempt_is_one_attempt_in_the_results(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("caliper.retry.RetryPolicy", lambda: NO_WAIT)
    harness = ThrottleThenPassHarness()

    results = run(
        spec=_spec(),
        spec_path=_spec_file(tmp_path),
        harness=harness,
        judge=PassingJudge(),
        k=3,
        workers=1,
        timeout=5,
    )

    attempts = results.task_results[0].attempts
    # Six invocations, three attempts: the throttled halves measured nothing.
    assert harness.invocations == 6
    assert [a.attempt for a in attempts] == [1, 2, 3]
    assert [a.outcome for a in attempts] == [Outcome.PASS] * 3
    assert [a.retries for a in attempts] == [1, 1, 1]
    # The denominator the score divides by is attempts, not spawns.
    assert results.task_results[0].score == 1.0


def test_a_spending_cap_aborts_the_run_and_saves_what_ran(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr("caliper.retry.RetryPolicy", lambda: NO_WAIT)

    with pytest.raises(RunAborted) as excinfo:
        run(
            spec=_spec(),
            spec_path=_spec_file(tmp_path),
            harness=CappedHarness(),
            judge=PassingJudge(),
            k=4,
            workers=1,
            timeout=5,
        )

    aborted = excinfo.value
    assert isinstance(aborted.cause, SpendingCapReached)
    assert aborted.results.run.interrupted is True
    # The capped attempt measured nothing and the run stopped, rather than
    # spending the other three discovering the same wall.
    assert aborted.results.task_results[0].attempts == []


class AlwaysThrottledHarness(HarnessBackend):
    def __init__(self) -> None:
        self.invocations = 0

    @property
    def name(self) -> str:
        return "throttled"

    def run(self, task_id: str, attempt: int, prompt: str, **kwargs) -> AttemptResult:
        self.invocations += 1
        return _result(**THROTTLED)


def test_fail_fast_counts_attempts_not_invocations(tmp_path, monkeypatch) -> None:
    """`--fail-fast 2` means two dead attempts, however many spawns they cost."""
    monkeypatch.setattr("caliper.retry.RetryPolicy", lambda: NO_WAIT)
    harness = AlwaysThrottledHarness()

    results = run(
        spec=_spec(),
        spec_path=_spec_file(tmp_path),
        harness=harness,
        judge=PassingJudge(),
        k=6,
        workers=1,
        timeout=5,
        fail_fast_unusable=2,
    )

    attempts = results.task_results[0].attempts
    assert [a.outcome for a in attempts] == [Outcome.INFRA_ERROR] * 2
    assert [a.retries for a in attempts] == [2, 2]
    # Two attempts, three invocations each — the flag bounds attempts, and the
    # inflation in spawns is the price of the retry, stated rather than hidden.
    assert harness.invocations == 6


def test_a_run_that_measured_nothing_is_not_saved(tmp_path, monkeypatch) -> None:
    """An empty run would read as 0.0% in `caliper list`, which is a lie."""
    monkeypatch.setattr("caliper.retry.RetryPolicy", lambda: NO_WAIT)
    spec_file = tmp_path / "sample.eval.yaml"
    spec_file.write_text(
        "tasks:\n  - name: One\n    prompt: Do it\n    assert: 'assert True'\n"
    )
    monkeypatch.setattr(
        "caliper.commands.run.get_harness", lambda *a, **kw: CappedHarness()
    )
    monkeypatch.setattr(
        "caliper.commands.run.EvalJudge", lambda *a, **kw: PassingJudge()
    )

    from typer.testing import CliRunner

    from caliper.main import app

    result = CliRunner().invoke(app, ["run", str(spec_file)])

    assert result.exit_code == 2
    assert "Spending cap" in result.stdout
    assert not (tmp_path / ".caliper").exists()


# ---------------------------------------------------------------------------
# A signal in an *answer* is prose, not an outcome. caliper evaluates skills,
# and a plausible eval task is about API error handling — so an agent writing
# "handle the rate limit" is the expected case, not an edge case.
# ---------------------------------------------------------------------------

ANSWER_ABOUT_LIMITS = (
    "I added a retry wrapper around the client. When the API returns a rate "
    "limit error (HTTP 429) the call now backs off and retries twice before "
    "surfacing the failure, and I extended the same handling to the quota "
    "exceeded case so a usage limit no longer crashes the ingest job. The "
    "tests cover both paths, including the 503 service unavailable branch, "
    "and I ran the suite to confirm nothing else regressed by these changes."
)


def test_a_passing_attempt_that_mentions_a_cap_does_not_abort_the_run() -> None:
    invoke, calls = _queue(_result(output=ANSWER_ABOUT_LIMITS))

    invoked = invoke_with_retry(invoke, NO_WAIT)

    assert len(calls) == 1
    assert invoked.retries == 0
    assert invoked.result.final_output == ANSWER_ABOUT_LIMITS


def test_a_passing_attempt_that_mentions_a_throttle_is_not_respawned() -> None:
    """Retrying here would triple the spend of an attempt that already worked."""
    invoke, calls = _queue(_result(output=ANSWER_ABOUT_LIMITS), _result())

    invoke_with_retry(invoke, NO_WAIT)

    assert len(calls) == 1


def test_a_capped_cli_is_still_caught_when_the_message_is_the_whole_output() -> None:
    """The shape the detection exists for: exit 0, cap message, nothing parsed."""
    invoke, _ = _queue(
        _result(output="Your spending cap has been reached.", salvaged=True)
    )

    with pytest.raises(SpendingCapReached):
        invoke_with_retry(invoke, NO_WAIT)


def test_a_timeout_carrying_throttle_text_is_still_not_retried() -> None:
    """Explicit, not incidental: partial output must not earn a respawn."""
    invoke, calls = _queue(
        _result(
            output="429 rate limit",
            error="timeout",
            exit_code=124,
            timed_out=True,
        ),
        _result(),
    )

    invoked = invoke_with_retry(invoke, NO_WAIT)

    assert len(calls) == 1
    assert invoked.result.timed_out is True
