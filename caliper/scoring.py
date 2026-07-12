"""Caliper's scoring module — the one place the usable-only denominator lives.

Every metric divides by *usable* attempts (those that got a fair shot), never
by raw k — see docs/adr/0007-raw-success-rate-is-the-primary-metric.md and
docs/CONTEXT.md → Usable / unusable attempt. ``score_outcomes`` is the seam
where a task's attempt outcomes become counts and metrics; callers should go
through it rather than re-deriving ``usable`` themselves.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from caliper.schema.results import AggregateScore, Outcome, TaskScore


def success_rate(successes: int, usable: int) -> float | None:
    """The **raw** per-attempt success rate — Caliper's primary metric. ``None``
    when no attempt got a fair shot (usable == 0)."""
    return successes / usable if usable > 0 else None


def pass_at_k(successes: int, usable: int) -> float | None:
    """P(at least one of k attempts passes) at the observed rate. A secondary,
    retry-friendly view — see docs/CONTEXT.md → Success rate.

    The denominator (and exponent) is the *usable* attempt count, not the
    requested k. ``None`` when no attempt got a fair shot.
    """
    if usable == 0:
        return None
    p = successes / usable
    return 1.0 - (1.0 - p) ** usable


def pass_hat_k(successes: int, usable: int) -> float | None:
    """P(all k attempts pass) at the observed rate — the strict, consistency view.

    Same *usable* denominator and exponent as ``pass_at_k``; ``None`` when no
    attempt got a fair shot.
    """
    if usable == 0:
        return None
    return (successes / usable) ** usable


@dataclass(frozen=True)
class OutcomeScores:
    """The counts and every metric for one task's attempt outcomes."""

    successes: int
    usable: int
    unusable: int
    score: float | None
    pass_at_k: float | None
    pass_hat_k: float | None


def score_outcomes(outcomes: Iterable[Outcome]) -> OutcomeScores:
    """Score one task's attempt outcomes.

    The single step where attempts become metrics: split usable from unusable
    once, then compute every metric over the usable denominator. All three
    metrics are ``None`` when no attempt got a fair shot.
    """
    materialized = list(outcomes)
    successes = sum(1 for o in materialized if o == Outcome.PASS)
    usable = sum(1 for o in materialized if o.is_usable)
    return OutcomeScores(
        successes=successes,
        usable=usable,
        unusable=len(materialized) - usable,
        score=success_rate(successes, usable),
        pass_at_k=pass_at_k(successes, usable),
        pass_hat_k=pass_hat_k(successes, usable),
    )


def aggregate_scores(
    task_pass_counts: dict[str, tuple[str, int, int, int]],
) -> AggregateScore:
    """
    task_pass_counts: {task_id: (task_name, successes, usable, k)}

    ``score`` is the raw success rate over the *usable* attempts (those that got a
    fair shot). A task with no usable attempts scores ``None`` and is excluded
    from the aggregate average rather than dragged to 0%.
    """
    per_task: list[TaskScore] = []
    for task_id, (task_name, successes, usable, k) in task_pass_counts.items():
        per_task.append(
            TaskScore(
                task_id=task_id,
                task_name=task_name,
                k=k,
                successes=successes,
                score=success_rate(successes, usable),
            )
        )

    scored = [t.score for t in per_task if t.score is not None]
    avg = sum(scored) / len(scored) if scored else 0.0
    return AggregateScore(avg_score=avg, per_task=per_task)
