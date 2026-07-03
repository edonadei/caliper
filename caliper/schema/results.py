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
    # The judge engine that graded this run. Optional so results saved before
    # judge provenance was recorded still load (they render as an unknown judge).
    judge_backend: str | None = None
    judge_model: str | None = None


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


class TaskComparison(BaseModel):
    """One matched task diffed across two runs (A vs B).

    ``a_score``/``b_score`` are the stored per-task ``pass_at_k`` (``None`` when
    every attempt was unusable — the task was never fairly measured). ``delta``
    is ``b - a`` only when both sides were measured, else ``None`` (never faked
    as 0). ``regression`` fires on the any-below rule: B below A, both measured.
    """

    task_name: str
    a_score: float | None
    b_score: float | None
    delta: float | None
    regression: bool
    a_outcomes: list[Outcome]
    b_outcomes: list[Outcome]


class RunComparison(BaseModel):
    """The pure result of ``diff_runs(a, b)`` — the whole ``compare`` contract.

    Rendering (table) and ``--format json`` are thin shells over this value.
    Usable/unusable counts are intentionally *not* stored: they are derivable
    from ``a_outcomes``/``b_outcomes`` (see docs/adr/0001-attempt-outcome-taxonomy.md).
    """

    a: RunMeta
    b: RunMeta
    matched: list[TaskComparison]
    unmatched_a: list[str]
    unmatched_b: list[str]
    # Aggregate over the fully-comparable set (tasks measured on *both* sides).
    a_matched_avg: float
    b_matched_avg: float
    aggregate_delta: float
    has_regression: bool
    k_mismatch: bool
    spec_mismatch: bool
    # Human-readable guards, mirrored into both the table header and JSON so an
    # agent on --format json sees the exact warning a human sees.
    warnings: list[str]
