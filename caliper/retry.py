"""Retrying a throttled invocation, and the merge that keeps it one attempt.

An attempt is one *measured shot at the task*; an invocation is one spawn of the
agent. Usually they are the same thing. They stop being the same thing when the
provider says "not now" — a 429, an overload, a 503 — because that invocation
produced no measurement at all. Counting it as an attempt would put the
provider's queue depth into the denominator the score divides by.

So this module retries the invocation and hands back **one** result:
:func:`invoke_with_retry` loops, and :func:`merge` folds what it collected into a
single :class:`AttemptResult`. The merge rule is what makes the arithmetic
honest — see its docstring. See
docs/adr/0019-an-attempt-may-be-invoked-more-than-once.md.

A spending cap is deliberately *not* retried here: it does not clear inside a
run, so it raises :class:`SpendingCapReached` and the runner stops the whole run
rather than spending every remaining attempt on the same wall.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, replace
from typing import Callable

from caliper import cancel
from caliper.harness.base import AttemptResult
from caliper.outcome import looks_like_spending_cap, looks_like_throttle
from caliper.schema.results import TokenUsage


class SpendingCapReached(RuntimeError):
    """The account is out of budget — fatal for the run, not just this attempt."""


@dataclass(frozen=True)
class RetryPolicy:
    """How hard to try again, and how long to wait between tries.

    Deliberately not exposed as CLI flags: the numbers are small, bounded, and
    the same for everyone. A run that needs more than this is throttled hard
    enough that waiting is not the answer.
    """

    # Extra invocations after the first. 2 is enough to ride out a burst limit
    # without turning one wedged attempt into a minute of sleeping.
    max_retries: int = 2
    # 2s, then 4s. Doubling from a base that is already longer than a token
    # bucket's refill, so the second wait is a real one rather than two hopeful
    # ones.
    base_delay_seconds: float = 2.0
    # Up to +25%, so parallel workers that were throttled together do not all
    # come back at the same instant and re-throttle each other.
    jitter: float = 0.25

    def delay_for(self, retry_index: int) -> float:
        """Seconds to wait before retry number ``retry_index`` (0-based)."""
        base = self.base_delay_seconds * (2**retry_index)
        return base * (1 + random.random() * self.jitter)


def _text_of(result: AttemptResult) -> str:
    return "\n".join(part for part in (result.final_output, result.error) if part)


def merge(invocations: list[AttemptResult]) -> AttemptResult:
    """Fold every invocation of one attempt into the attempt's single result.

    Three different rules, one per kind of field, and each is a claim:

    * **What happened** — transcript, output, exit code, timeout flag, resolved
      model — comes from the **last** invocation. The earlier ones produced no
      measurement, which is precisely why they were retried; keeping their
      output would report a throttle message as the agent's answer.
    * **What it cost** — tokens — is **summed**. Those tokens were really spent,
      even by an invocation that died holding them, and hiding them would make a
      heavily-retried run look cheaper than a clean one.
    * **How long it took** — ``duration_seconds`` — is the **sum of the spawns**
      and excludes the waiting between them. The field is pinned to time spent
      inside ``_execute`` (docs/CONTEXT.md → Wall-clock time); folding backoff
      into it would turn a latency figure into a measure of the provider's
      queue, and stop it comparing across runs.
    """
    last = invocations[-1]
    if len(invocations) == 1:
        return last
    return replace(
        last,
        duration_seconds=sum(inv.duration_seconds for inv in invocations),
        usage=_merge_usage([inv.usage for inv in invocations]),
    )


def _merge_usage(usages: list[TokenUsage | None]) -> TokenUsage | None:
    """Sum the reported token fields; ``None`` when no invocation reported any.

    Each field is summed only over the invocations that reported it, so a
    backend that reports nothing stays ``None`` (renders "—") rather than
    becoming a confident zero.
    """
    reported = [u for u in usages if u is not None]
    if not reported:
        return None

    def total(field: str) -> int | None:
        values = [getattr(u, field) for u in reported]
        present = [v for v in values if v is not None]
        return sum(present) if present else None

    return TokenUsage(
        input_tokens=total("input_tokens"),
        output_tokens=total("output_tokens"),
        cache_read_tokens=total("cache_read_tokens"),
        cache_creation_tokens=total("cache_creation_tokens"),
    )


@dataclass(frozen=True)
class RetriedInvocation:
    """One attempt's result, plus how many extra invocations it took to get it."""

    result: AttemptResult
    retries: int


def invoke_with_retry(
    invoke: Callable[[], AttemptResult],
    policy: RetryPolicy | None = None,
) -> RetriedInvocation:
    """Run one attempt, retrying while the provider says it is too busy.

    Raises :class:`SpendingCapReached` the moment a cap is seen — before any
    wait, since waiting cannot help. A cancellation during a backoff ends the
    retrying immediately and returns what it has: the run is stopping anyway.

    Only a *throttle* is retried. A timeout is not (nothing says the next spawn
    would be faster), and neither is a bare non-zero exit with no signal in it —
    that is a crash, and retrying one hides a defect that reproduces.
    """
    policy = policy or RetryPolicy()
    invocations: list[AttemptResult] = []

    for retry_index in range(policy.max_retries + 1):
        result = invoke()
        invocations.append(result)
        text = _text_of(result)

        if looks_like_spending_cap(text):
            raise SpendingCapReached(
                "The provider reports a spending cap or usage limit reached:\n\n"
                f"  {text.strip().splitlines()[0][:200]}\n\n"
                "Every remaining attempt would meet the same wall, so the run "
                "stopped here rather than spending them to find that out."
            )
        if not looks_like_throttle(text):
            break
        if retry_index == policy.max_retries:
            break
        if cancel.sleep(policy.delay_for(retry_index)):
            break

    return RetriedInvocation(merge(invocations), retries=len(invocations) - 1)
