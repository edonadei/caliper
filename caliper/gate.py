"""The gate: whether a run cleared the bar its spec pre-registered.

A bar is a number a clean run must reach, committed in the spec's ``bar:``
before the run produced a score (docs/adr/0035). Checking a rate against it
naively — the point estimate against the bar — blocks on noise at the sample
sizes CI can afford: 4/5 and 5/5 are both plausible draws from one skill. So
each rate carries a 95% Wilson interval over its usable attempts, and the
verdict is about the whole interval:

- **cleared** — the interval sits at or above the bar;
- **missed** — the interval sits entirely below it (exit 3);
- **inconclusive** — the interval straddles it. The spec decides what that
  exits with (``on_inconclusive``), because "not enough evidence" is a policy
  question, not a statistical one.

Rates are pooled over attempts across tasks — the same usable denominator as
every other number (docs/adr/0001) — rather than averaged over tasks, because an
interval needs a count of trials. With every task run to k and no unusable
attempts, the pooled rate is the headline score.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum

from caliper.schema.results import RunResults

# Two-sided 95%: the interval a reader assumes when none is named.
_Z = 1.959963984540054


class Verdict(str, Enum):
    CLEARED = "cleared"
    MISSED = "missed"
    INCONCLUSIVE = "inconclusive"
    # The run was not the kind a bar speaks to (ablated, interrupted, a failed
    # hook); see ``GateResult.reason``.
    NOT_APPLIED = "not_applied"


def wilson_interval(successes: int, trials: int) -> tuple[float, float]:
    """The 95% Wilson score interval for ``successes`` out of ``trials``.

    Wilson rather than the normal approximation because it stays inside
    ``[0, 1]`` and stays honest at 0/n and n/n, which small CI samples hit
    constantly. No trials says nothing: ``(0.0, 1.0)``.
    """
    if trials <= 0:
        return 0.0, 1.0
    p = successes / trials
    z2 = _Z * _Z
    denom = 1 + z2 / trials
    centre = (p + z2 / (2 * trials)) / denom
    half = _Z * math.sqrt(p * (1 - p) / trials + z2 / (4 * trials * trials)) / denom
    # Clamped against float drift: 5/5 must reach exactly 1.0, not 0.9999…
    low = 0.0 if successes == 0 else max(0.0, centre - half)
    high = 1.0 if successes == trials else min(1.0, centre + half)
    return low, high


@dataclass(frozen=True)
class RateCheck:
    """One barred rate: what was observed, its interval, and the verdict."""

    # "score" or "activation" — the ``bar:`` key it came from.
    name: str
    bar: float
    successes: int
    usable: int
    low: float
    high: float

    @property
    def rate(self) -> float | None:
        return self.successes / self.usable if self.usable else None

    @property
    def verdict(self) -> Verdict:
        if self.low >= self.bar:
            return Verdict.CLEARED
        if self.high < self.bar:
            return Verdict.MISSED
        return Verdict.INCONCLUSIVE


@dataclass(frozen=True)
class GateResult:
    checks: list[RateCheck]
    on_inconclusive: str = "pass"
    # Why the bar was not applied; set only with ``Verdict.NOT_APPLIED``.
    reason: str | None = None

    @property
    def verdict(self) -> Verdict:
        if self.reason is not None:
            return Verdict.NOT_APPLIED
        verdicts = {check.verdict for check in self.checks}
        for worst in (Verdict.MISSED, Verdict.INCONCLUSIVE):
            if worst in verdicts:
                return worst
        return Verdict.CLEARED

    @property
    def blocks(self) -> bool:
        """Whether this verdict fails the pipeline (exit 3)."""
        if self.verdict is Verdict.MISSED:
            return True
        return self.verdict is Verdict.INCONCLUSIVE and self.on_inconclusive == "fail"

    def to_json(self) -> dict:
        return {
            "verdict": self.verdict.value,
            "blocks": self.blocks,
            "on_inconclusive": self.on_inconclusive,
            "reason": self.reason,
            "checks": [
                {
                    "name": c.name,
                    "bar": c.bar,
                    "successes": c.successes,
                    "usable": c.usable,
                    "rate": c.rate,
                    "low": c.low,
                    "high": c.high,
                    "verdict": c.verdict.value,
                }
                for c in self.checks
            ],
        }


def evaluate(results: RunResults) -> GateResult | None:
    """The run's gate result, or ``None`` when its spec declared no bar.

    Read from ``RunMeta.bar`` — the bar recorded when the run started — so a
    saved run is judged by the bar it was held to, not by today's spec.
    """
    bar = results.run.bar
    if bar is None:
        return None
    run = results.run
    reason = None
    if run.ablated:
        # An ablated run measures what a subject contributes; the bar is about
        # the full environment the spec declares.
        reason = "ablated run (--ablate): the bar applies to the full spec"
    elif run.interrupted:
        reason = "interrupted run: the sample is shorter than the spec asked for"
    elif run.hook_failures:
        reason = "a setup/cleanup hook failed: the run is not a clean measurement"

    checks: list[RateCheck] = []
    tasks = results.task_results
    if bar.score is not None:
        successes = sum(t.successes for t in tasks)
        usable = sum(t.usable for t in tasks)
        checks.append(
            RateCheck(
                "score",
                bar.score,
                successes,
                usable,
                *wilson_interval(successes, usable),
            )
        )
    if bar.activation is not None:
        successes = sum(t.activation_successes for t in tasks)
        usable = sum(t.activation_usable for t in tasks)
        checks.append(
            RateCheck(
                "activation",
                bar.activation,
                successes,
                usable,
                *wilson_interval(successes, usable),
            )
        )
    return GateResult(checks=checks, on_inconclusive=bar.on_inconclusive, reason=reason)
