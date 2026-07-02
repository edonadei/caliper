from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field, computed_field


class Outcome(str, Enum):
    """The typed result of a single attempt.

    Usable outcomes got a fair shot at the task and count toward pass@k;
    the unusable ones are infrastructure/judge noise and are excluded from
    the denominator. See CONTEXT.md and docs/adr/0001-attempt-outcome-taxonomy.md.
    """

    PASS = "pass"
    TASK_FAIL = "task_fail"
    JUDGE_ERROR = "judge_error"
    INFRA_ERROR = "infra_error"
    TIMEOUT = "timeout"
    CHEAT = "cheat"

    @property
    def is_usable(self) -> bool:
        """True when the attempt was fairly measured (counts toward pass@k)."""
        return self in (Outcome.PASS, Outcome.TASK_FAIL, Outcome.CHEAT)


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


class AttemptRecord(BaseModel):
    attempt: int
    output: str
    duration_seconds: float
    outcome: Outcome
    cheat_evidence: list[str] = Field(default_factory=list)
    assert_passed: bool | None = None
    assert_evidence: str | None = None
    autorater_passed: bool | None = None
    autorater_reasoning: str | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def passed(self) -> bool:
        """Derived convenience: an attempt passed iff its outcome is ``pass``."""
        return self.outcome == Outcome.PASS

    @computed_field  # type: ignore[prop-decorator]
    @property
    def cheated(self) -> bool:
        return self.outcome == Outcome.CHEAT


class TaskResult(BaseModel):
    task_id: str
    task_name: str
    attempts: list[AttemptRecord]
    successes: int
    unusable: int = 0
    # None when every attempt was unusable — the task was never fairly measured.
    pass_at_k: float | None


class TaskScore(BaseModel):
    task_id: str
    task_name: str
    k: int
    successes: int
    # None when every attempt was unusable (excluded from the aggregate average).
    score: float | None


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
