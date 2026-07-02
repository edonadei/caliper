from __future__ import annotations

from dataclasses import dataclass

from caliper.judge.base import JudgeResult
from caliper.schema.results import AggregateScore, DeltaReport, Outcome, TaskScore


@dataclass(frozen=True)
class OutcomeScore:
    successes: int
    usable_attempts: int
    unusable_attempts: int
    pass_at_k: float


def pass_at_k(successes: int, k: int) -> float:
    if k == 0:
        return 0.0
    p = successes / k
    return 1.0 - (1.0 - p) ** k


UNUSABLE_OUTCOMES = {
    Outcome.INFRA_ERROR,
    Outcome.TIMEOUT,
    Outcome.JUDGE_ERROR,
}


def classify_outcome(
    *,
    exit_code: int,
    error: str | None,
    cheated: bool,
    judge_result: JudgeResult | None,
) -> Outcome:
    error_text = (error or "").lower()
    if exit_code == 124 or "timeout" in error_text or "timed out" in error_text:
        return Outcome.TIMEOUT
    if exit_code != 0 or error:
        return Outcome.INFRA_ERROR
    if cheated:
        return Outcome.CHEAT
    if judge_result is None:
        return Outcome.TASK_FAIL
    if judge_result.passed:
        return Outcome.PASS
    if _is_judge_error(judge_result):
        return Outcome.JUDGE_ERROR
    return Outcome.TASK_FAIL


def _is_judge_error(judge_result: JudgeResult) -> bool:
    text = " ".join(
        part
        for part in (
            judge_result.reasoning,
            judge_result.assert_evidence,
            judge_result.autorater_reasoning,
        )
        if part
    ).lower()
    markers = (
        "judge returned unparseable",
        "judge returned empty script",
        "judge timed out",
        "judge failed",
        "codex judge failed",
        "claude-api judge failed",
        "openai-api judge failed",
        "assertion script timed out",
    )
    return any(marker in text for marker in markers)


def score_outcomes(outcomes: list[Outcome]) -> OutcomeScore:
    successes = sum(1 for outcome in outcomes if outcome == Outcome.PASS)
    unusable_attempts = sum(1 for outcome in outcomes if outcome in UNUSABLE_OUTCOMES)
    usable_attempts = len(outcomes) - unusable_attempts
    return OutcomeScore(
        successes=successes,
        usable_attempts=usable_attempts,
        unusable_attempts=unusable_attempts,
        pass_at_k=pass_at_k(successes, usable_attempts),
    )


def aggregate_scores(
    task_pass_counts: dict[str, tuple[str, int, int] | tuple[str, int, int, int]],
) -> AggregateScore:
    """
    task_pass_counts: {task_id: (task_name, successes, usable_attempts[, unusable_attempts])}
    """
    per_task: list[TaskScore] = []
    total_unusable = 0
    for task_id, counts in task_pass_counts.items():
        task_name, successes, k = counts[:3]
        unusable_attempts = counts[3] if len(counts) > 3 else 0
        total_unusable += unusable_attempts
        score = pass_at_k(successes, k)
        per_task.append(
            TaskScore(
                task_id=task_id,
                task_name=task_name,
                k=k,
                successes=successes,
                score=score,
                unusable_attempts=unusable_attempts,
            )
        )

    avg = sum(t.score for t in per_task) / len(per_task) if per_task else 0.0
    return AggregateScore(
        avg_pass_at_k=avg, per_task=per_task, unusable_attempts=total_unusable
    )


def compute_delta(
    with_skill: AggregateScore, without_skill: AggregateScore
) -> DeltaReport:
    return DeltaReport(
        with_skill=with_skill,
        without_skill=without_skill,
        delta=with_skill.avg_pass_at_k - without_skill.avg_pass_at_k,
    )
