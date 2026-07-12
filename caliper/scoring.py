from __future__ import annotations

from caliper.schema.results import AggregateScore, TaskScore


def success_rate(successes: int, usable: int) -> float | None:
    """The **raw** per-attempt success rate — Caliper's primary metric. ``None``
    when no attempt got a fair shot (usable == 0)."""
    return successes / usable if usable > 0 else None


def pass_at_k(successes: int, k: int) -> float:
    """P(at least one of k attempts passes) at the observed rate. A secondary,
    retry-friendly view — see docs/CONTEXT.md → Success rate."""
    if k == 0:
        return 0.0
    p = successes / k
    return 1.0 - (1.0 - p) ** k


def pass_hat_k(successes: int, k: int) -> float | None:
    """P(all k attempts pass) at the observed rate — the strict, consistency view."""
    if k == 0:
        return None
    return (successes / k) ** k


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
