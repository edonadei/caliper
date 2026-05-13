from __future__ import annotations

from verdict.schema.results import AggregateScore, DeltaReport, TaskScore


def pass_at_k(successes: int, k: int) -> float:
    if k == 0:
        return 0.0
    p = successes / k
    return 1.0 - (1.0 - p) ** k


def aggregate_scores(task_pass_counts: dict[str, tuple[str, int, int]]) -> AggregateScore:
    """
    task_pass_counts: {task_id: (task_name, successes, k)}
    """
    per_task: list[TaskScore] = []
    for task_id, (task_name, successes, k) in task_pass_counts.items():
        score = pass_at_k(successes, k)
        per_task.append(
            TaskScore(task_id=task_id, task_name=task_name, k=k, successes=successes, score=score)
        )

    avg = sum(t.score for t in per_task) / len(per_task) if per_task else 0.0
    return AggregateScore(avg_pass_at_k=avg, per_task=per_task)


def compute_delta(with_skill: AggregateScore, without_skill: AggregateScore) -> DeltaReport:
    return DeltaReport(
        with_skill=with_skill,
        without_skill=without_skill,
        delta=with_skill.avg_pass_at_k - without_skill.avg_pass_at_k,
    )
