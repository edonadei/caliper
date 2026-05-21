from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


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
    judge_strategy: str
    backend: str
    model: str | None = None


class AttemptRecord(BaseModel):
    attempt: int
    output: str
    duration_seconds: float
    passed: bool
    cheated: bool = False
    cheat_evidence: list[str] = Field(default_factory=list)
    assert_passed: bool | None = None
    assert_evidence: str | None = None
    autorater_passed: bool | None = None
    autorater_reasoning: str | None = None


class TaskResult(BaseModel):
    task_id: str
    task_name: str
    attempts: list[AttemptRecord]
    successes: int
    pass_at_k: float


class TaskScore(BaseModel):
    task_id: str
    task_name: str
    k: int
    successes: int
    score: float


class AggregateScore(BaseModel):
    avg_pass_at_k: float
    per_task: list[TaskScore]


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
