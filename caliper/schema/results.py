from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field, model_validator


class FileSnapshot(BaseModel):
    content: str
    hash: str


class SkillSnapshot(BaseModel):
    path: str
    git_repo: str | None = None
    git_sha: str | None = None
    files: dict[str, FileSnapshot] = Field(default_factory=dict)


class RunMeta(BaseModel):
    spec: str
    timestamp: datetime
    k: int
    backend: str
    model: str | None = None


class Outcome(str, Enum):
    PASS = "pass"
    TASK_FAIL = "task_fail"
    JUDGE_ERROR = "judge_error"
    INFRA_ERROR = "infra_error"
    TIMEOUT = "timeout"
    CHEAT = "cheat"


class AttemptRecord(BaseModel):
    attempt: int
    output: str
    duration_seconds: float
    passed: bool
    outcome: Outcome | None = None
    cheated: bool = False
    cheat_evidence: list[str] = Field(default_factory=list)
    assert_passed: bool | None = None
    assert_evidence: str | None = None
    autorater_passed: bool | None = None
    autorater_reasoning: str | None = None

    @model_validator(mode="after")
    def sync_outcome_and_passed(self) -> AttemptRecord:
        if self.outcome is None:
            if self.passed:
                self.outcome = Outcome.PASS
            elif self.cheated:
                self.outcome = Outcome.CHEAT
            else:
                self.outcome = Outcome.TASK_FAIL

        self.passed = self.outcome == Outcome.PASS
        if self.outcome == Outcome.CHEAT:
            self.cheated = True
        return self


class TaskResult(BaseModel):
    task_id: str
    task_name: str
    attempts: list[AttemptRecord]
    successes: int
    pass_at_k: float
    unusable_attempts: int = 0


class TaskScore(BaseModel):
    task_id: str
    task_name: str
    k: int
    successes: int
    score: float
    unusable_attempts: int = 0


class AggregateScore(BaseModel):
    avg_pass_at_k: float
    per_task: list[TaskScore]
    unusable_attempts: int = 0


class DeltaReport(BaseModel):
    with_skill: AggregateScore
    without_skill: AggregateScore
    delta: float


class RunResults(BaseModel):
    run: RunMeta
    skill_snapshot: SkillSnapshot
    task_results: list[TaskResult]
    aggregate: AggregateScore
    baseline: AggregateScore | None = None
    delta: DeltaReport | None = None
