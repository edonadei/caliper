from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field, computed_field


class Outcome(str, Enum):
    """The typed result of a single attempt.

    Usable outcomes got a fair shot at the task and count toward pass@k;
    the unusable ones are infrastructure/judge noise and are excluded from
    the denominator. See docs/CONTEXT.md and docs/adr/0001-attempt-outcome-taxonomy.md.
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


class TokenUsage(BaseModel):
    """The token accounting of a single attempt; all fields optional.

    A backend that cannot report usage leaves every field ``None`` (rendered as
    "—"), exactly like ``resolved_model``. The four token fields are **disjoint**
    — ``input_tokens`` is *non-cached* prompt tokens only, cache lives solely in
    ``cache_read_tokens``/``cache_creation_tokens`` — so ``total_tokens`` (their
    sum) never double-counts. Backends are normalized into this contract (codex
    subtracts its cached tokens from ``input_tokens``). Dollar cost is deliberately
    out of scope; see docs/adr/0006 and docs/CONTEXT.md → Attempt usage.
    """

    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_creation_tokens: int | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total_tokens(self) -> int | None:
        """Sum of the four disjoint token fields, or ``None`` if none reported."""
        parts = [
            self.input_tokens,
            self.output_tokens,
            self.cache_read_tokens,
            self.cache_creation_tokens,
        ]
        reported = [p for p in parts if p is not None]
        return sum(reported) if reported else None


class FileSnapshot(BaseModel):
    content: str
    hash: str


class SkillSnapshot(BaseModel):
    path: str
    git_repo: str | None = None
    git_sha: str | None = None
    files: dict[str, FileSnapshot] = Field(default_factory=dict)


class TranscriptTurn(BaseModel):
    """One turn in an attempt's conversation, including tool calls when present."""

    role: str
    content: str
    tool_name: str | None = None
    tool_input: dict | None = None
    tool_output: str | None = None


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
    # Token accounting for this attempt, when the backend reports it. Optional so
    # results saved before usage tracking still load (they render as "—").
    usage: TokenUsage | None = None
    # Ordered conversation turns, including tool_use/tool_result when present.
    # Optional so results saved before transcript persistence still load.
    transcript: list[TranscriptTurn] | None = None
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
    # pass@k (P(≥1 of k pass)) — kept as a secondary, retry-friendly view. The
    # *primary* metric is ``score`` (raw success rate) below. None when every
    # attempt was unusable — the task was never fairly measured.
    pass_at_k: float | None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def usable(self) -> int:
        """Attempts that got a fair shot (the pass@k / rate denominator)."""
        return len(self.attempts) - self.unusable

    @computed_field  # type: ignore[prop-decorator]
    @property
    def score(self) -> float | None:
        """The **raw success rate** over usable attempts — Caliper's primary
        metric. ``None`` when no attempt was fairly measured."""
        # Deferred import: caliper.scoring imports this module, and the formulas
        # deliberately live there — the one place the usable denominator is set.
        from caliper.scoring import success_rate

        return success_rate(self.successes, self.usable)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def pass_hat_k(self) -> float | None:
        """pass^k: P(all usable attempts pass) — the strict consistency view."""
        from caliper.scoring import pass_hat_k

        return pass_hat_k(self.successes, self.usable)


class UsageTotals(BaseModel):
    """Run-level roll-up of per-attempt token usage + wall-clock time.

    Always **derived** from the attempt records (``from_task_results``), never
    persisted on ``RunResults`` — see docs/CONTEXT.md → Run usage totals. Every attempt
    counts toward the totals (the tokens/time were really spent), and the
    unusable-attempt subset is tracked separately so wasted spend is visible
    without distorting the per-usable-attempt average. Used by both the single-run
    report and ``compare``.
    """

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    wall_seconds: float = 0.0
    attempts: int = 0
    # False when no attempt reported any token field — the token figures are then
    # meaningless and render as "—" (wall time is always real).
    tokens_reported: bool = False
    # The unusable-attempt subset of the totals above, reported on its own line.
    unusable_tokens: int = 0
    unusable_wall_seconds: float = 0.0
    unusable_attempts: int = 0

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total_tokens(self) -> int:
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_read_tokens
            + self.cache_creation_tokens
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def prompt_tokens(self) -> int:
        """The "in" figure: non-cached input plus all cache tokens."""
        return self.input_tokens + self.cache_read_tokens + self.cache_creation_tokens

    @computed_field  # type: ignore[prop-decorator]
    @property
    def usable_attempts(self) -> int:
        return self.attempts - self.unusable_attempts

    @computed_field  # type: ignore[prop-decorator]
    @property
    def usable_wall_seconds(self) -> float:
        return self.wall_seconds - self.unusable_wall_seconds

    @classmethod
    def from_task_results(cls, task_results: list[TaskResult]) -> UsageTotals:
        totals = cls()
        for tr in task_results:
            for att in tr.attempts:
                totals.attempts += 1
                totals.wall_seconds += att.duration_seconds
                unusable = not att.outcome.is_usable
                if unusable:
                    totals.unusable_attempts += 1
                    totals.unusable_wall_seconds += att.duration_seconds
                u = att.usage
                if u is None:
                    continue
                fields = (
                    u.input_tokens,
                    u.output_tokens,
                    u.cache_read_tokens,
                    u.cache_creation_tokens,
                )
                if all(f is None for f in fields):
                    continue
                totals.tokens_reported = True
                totals.input_tokens += u.input_tokens or 0
                totals.output_tokens += u.output_tokens or 0
                totals.cache_read_tokens += u.cache_read_tokens or 0
                totals.cache_creation_tokens += u.cache_creation_tokens or 0
                if unusable:
                    totals.unusable_tokens += u.total_tokens or 0
        return totals


class TaskScore(BaseModel):
    task_id: str
    task_name: str
    k: int
    successes: int
    # The raw success rate (Caliper's primary metric). None when every attempt was
    # unusable (excluded from the aggregate average).
    score: float | None


class AggregateScore(BaseModel):
    # Average raw success rate over measured tasks (the primary aggregate).
    avg_score: float
    per_task: list[TaskScore]


class RunResults(BaseModel):
    run: RunMeta
    skill_snapshot: SkillSnapshot
    task_results: list[TaskResult]
    aggregate: AggregateScore
    # The **full** no-skill run, kept only when ``--baseline`` ran. Retaining the
    # whole run (not just a pass@k aggregate) lets a ``--baseline`` report render
    # through the very same ``compare`` machinery — same table, same strips, same
    # token/wall deltas — instead of a bespoke renderer. Optional so old JSON (and
    # non-baseline runs) still load.
    baseline_task_results: list[TaskResult] | None = None


class TaskComparison(BaseModel):
    """One matched task diffed across two runs (A vs B).

    ``a_score``/``b_score`` are the stored per-task ``pass_at_k`` (``None`` when
    every attempt was unusable — the task was never fairly measured). ``delta``
    is ``b - a`` only when both sides were measured, else ``None`` (never faked
    as 0). ``regression`` fires when B drops more than the comparison's
    ``regression_margin`` below A (any-below when that margin is 0).
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
    # How each side is titled in the header. ``None`` → the run's timestamp+engine
    # (the default for ``compare`` of two saved runs); a ``--baseline`` diff sets
    # them to "no skill" / "with skill" since both sides share one RunMeta.
    a_label: str | None = None
    b_label: str | None = None
    matched: list[TaskComparison]
    unmatched_a: list[str]
    unmatched_b: list[str]
    # Aggregate over the fully-comparable set (tasks measured on *both* sides).
    a_matched_avg: float
    b_matched_avg: float
    aggregate_delta: float
    # Effective non-inferiority tolerance in percentage points (5.0 = 5%). Zero
    # preserves the any-below regression rule.
    regression_margin: float = 0.0
    has_regression: bool
    k_mismatch: bool
    spec_mismatch: bool
    # Human-readable guards, mirrored into both the table header and JSON so an
    # agent on --format json sees the exact warning a human sees.
    warnings: list[str]
    # Run usage totals per side (all tasks, not just matched). Token/wall deltas
    # are shown alongside pass@k but NEVER feed has_regression — a token drop is a
    # win, not a failure (docs/CONTEXT.md → Regression).
    a_usage: UsageTotals
    b_usage: UsageTotals

    @computed_field  # type: ignore[prop-decorator]
    @property
    def token_delta(self) -> int:
        return self.b_usage.total_tokens - self.a_usage.total_tokens

    @computed_field  # type: ignore[prop-decorator]
    @property
    def wall_delta(self) -> float:
        return self.b_usage.wall_seconds - self.a_usage.wall_seconds
