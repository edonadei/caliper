from __future__ import annotations

from caliper.schema.results import AggregateScore, DeltaReport, TaskScore


def pass_at_k(successes: int, k: int) -> float:
    if k == 0:
        return 0.0
    p = successes / k
    return 1.0 - (1.0 - p) ** k


def aggregate_scores(task_pass_counts: dict[str, tuple[str, int, int, int]]) -> AggregateScore:
    """
    task_pass_counts: {task_id: (task_name, successes, usable, k)}

    ``score`` is pass@k over the *usable* attempts (those that got a fair shot).
    A task with no usable attempts scores ``None`` and is excluded from the
    aggregate average rather than dragged to 0%.
    """
    per_task: list[TaskScore] = []
    for task_id, (task_name, successes, usable, k) in task_pass_counts.items():
        score = pass_at_k(successes, usable) if usable > 0 else None
        per_task.append(
            TaskScore(task_id=task_id, task_name=task_name, k=k, successes=successes, score=score)
        )

    scored = [t.score for t in per_task if t.score is not None]
    avg = sum(scored) / len(scored) if scored else 0.0
    return AggregateScore(avg_pass_at_k=avg, per_task=per_task)


def compute_delta(with_skill: AggregateScore, without_skill: AggregateScore) -> DeltaReport:
    return DeltaReport(
        with_skill=with_skill,
        without_skill=without_skill,
        delta=with_skill.avg_pass_at_k - without_skill.avg_pass_at_k,
    )
