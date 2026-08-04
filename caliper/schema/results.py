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
    # The attempt ran cleanly and no execution check was authored — an
    # `activates:`-only task, whose whole claim is about what the agent reached
    # for. Not an error and not a failure: nothing was asked, so nothing is
    # answered. Distinct from `judge_error`, where a check existed and the
    # grader broke. See docs/adr/0001 and docs/adr/0014.
    NOT_CHECKED = "not_checked"

    @property
    def is_usable(self) -> bool:
        """True when the attempt was fairly measured (counts toward pass@k).

        ``NOT_CHECKED`` is excluded — but as *unasked*, not as noise. The
        execution score is over checks that were made, and a task that made none
        renders skipped rather than joining the unusable-attempt count.
        """
        return self in (Outcome.PASS, Outcome.TASK_FAIL, Outcome.CHEAT)

    @property
    def is_execution_noise(self) -> bool:
        """True for the outcomes that mean an execution check *went wrong*.

        The unusable-attempt report counts these; ``NOT_CHECKED`` is unusable but
        not noise, so a correct activates-only spec never reports an error.
        """
        return self in (
            Outcome.JUDGE_ERROR,
            Outcome.INFRA_ERROR,
            Outcome.TIMEOUT,
        )

    @property
    def is_activation_usable(self) -> bool:
        """True when this attempt's activation observation can be trusted.

        Deliberately *not* ``is_usable``. A ``judge_error`` is activation-usable
        — the agent ran, the transcript is whole, and only the grader broke, so
        excluding it would let a flaky autorater shrink the activation sample for
        a reason with no causal connection to it. Only ``infra_error`` and
        ``timeout`` are excluded: there the transcript may be truncated, and an
        empty observed set would be a fabricated "the description never fired".
        See docs/adr/0014 and docs/CONTEXT.md → Activation score.
        """
        return self not in (Outcome.INFRA_ERROR, Outcome.TIMEOUT)


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
    # The frontmatter name — the skill's identity, and the directory caliper
    # installed it at. Empty on pre-#18 runs, which had no stable identity.
    name: str = ""
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


# The loading discipline a run was produced under. Recorded explicitly rather
# than inferred from schema shape: the era is a *semantic* fact that merely
# correlates with a field layout today, and the next schema change would
# silently break the inference. ``None`` marks the pre-#18 runs — claude-code
# measured invocation x execution under a mangled skill name, the other backends
# measured execution with the skill force-loaded — which are comparable to
# neither each other nor anything since. See docs/adr/0013.
ERA_INSTALL_AND_DISCOVER = "install-and-discover"


class RunMeta(BaseModel):
    spec: str
    timestamp: datetime
    k: int
    backend: str
    model: str | None = None
    # ``None`` = a legacy run; ``compare`` refuses to diff across this boundary.
    era: str | None = None
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
    # The skills the agent chose to bring into context, recorded on every
    # attempt whether or not the task asserted on it. ``None`` means *not
    # observed* — no detector for this backend, or no transcript to read — and
    # renders "—", the idiom already used for ``resolved_model`` and
    # ``TokenUsage``. A bare ``[]`` would let an infrastructure failure render as
    # a confident "nothing fired". Distinct from ``activation_passed``'s
    # ``None``, which means *not asserted* (the ``assert_passed`` idiom).
    activated: list[str] | None = None
    activation_passed: bool | None = None

    @property
    def activation_scored(self) -> bool:
        """Whether this attempt counts on the activation scoreboard.

        Both halves matter and are easy to drift apart if rewritten per call
        site: the outcome must be activation-usable *and* the task must have
        asserted (``activation_passed is not None``).
        """
        return self.outcome.is_activation_usable and self.activation_passed is not None

    @property
    def activation_observed(self) -> bool:
        """Whether this attempt yielded a trustworthy observation to *display*.

        Weaker than :attr:`activation_scored`: an unasserted task still shows
        what loaded, dimmed.
        """
        return self.outcome.is_activation_usable and self.activated is not None

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
    # The task's `activates:` set, carried so the aggregate can compute per-skill
    # recall/precision and the report can say *what* was expected when a row
    # fails. ``None`` = the task asserted nothing.
    activation_expected: list[str] | None = None
    # pass@k (P(≥1 of k pass)) — kept as a secondary, retry-friendly view. The
    # *primary* metric is ``score`` (raw success rate) below. None when every
    # attempt was unusable — the task was never fairly measured.
    pass_at_k: float | None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def usable(self) -> int:
        """Attempts that got a fair shot (the pass@k / rate denominator).

        Derived from the attempts rather than ``len - unusable``: since
        ``NOT_CHECKED`` is neither usable nor noise, that subtraction would
        silently over-count.
        """
        return sum(1 for a in self.attempts if a.outcome.is_usable)

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

    # --- the second scoreboard, which never merges with the first ----------
    #
    # Derived from the attempts rather than stored, so the two denominators can
    # never drift apart. An attempt counts here only when it was both
    # activation-usable *and* asserted on, which is what makes an unasserted
    # task render "skipped" rather than 0%.

    @computed_field  # type: ignore[prop-decorator]
    @property
    def activation_usable(self) -> int:
        return sum(1 for a in self.attempts if a.activation_scored)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def activation_successes(self) -> int:
        return sum(
            1 for a in self.attempts if a.activation_scored and a.activation_passed
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def activation_score(self) -> float | None:
        """Exact-set-match rate over activation-usable, asserted attempts.

        ``None`` when the task asserted nothing (or nothing was measurable) —
        rendered *skipped*, never ``0%``.
        """
        from caliper.scoring import success_rate

        return success_rate(self.activation_successes, self.activation_usable)


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
                # Noise, not merely "not usable": a NOT_CHECKED trigger probe
                # spent its tokens producing a real activation measurement, so
                # it is not wasted spend.
                unusable = att.outcome.is_execution_noise
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


class SkillActivationStats(BaseModel):
    """Per-skill recall and precision, counted over attempts.

    Indexed by skill *name* rather than by role, because the thing an author
    edits in response is one skill's ``description``. Counted per attempt so the
    diagnostic shares units with the rates above it, and computed only over
    activation-usable attempts of tasks that asserted ``activates:``.
    """

    skill: str
    # Every activation-scored attempt this skill was in scope for. The
    # denominator both directions are carved out of.
    total: int = 0
    # Attempts where this skill was in the expected set.
    expected: int
    # Attempts where it was observed to activate.
    fired: int
    # Attempts where both were true.
    hits: int

    @computed_field  # type: ignore[prop-decorator]
    @property
    def recall(self) -> float | None:
        """How often it fired when it was meant to. ``None`` if never expected."""
        return self.hits / self.expected if self.expected else None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def precision(self) -> float | None:
        """How often it was meant to when it fired. ``None`` if it never fired."""
        return self.hits / self.fired if self.fired else None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def opportunities(self) -> int:
        """Attempts that did *not* want this skill: its chances to over-fire.

        The denominator of ``unwanted_rate``, exposed so the reporter renders the
        same number the rate divides by instead of recomputing it.
        """
        return max(self.total - self.expected, 0)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def unwanted(self) -> int:
        """Attempts where it fired and was *not* expected: the hijacks."""
        return self.fired - self.hits

    @computed_field  # type: ignore[prop-decorator]
    @property
    def unwanted_rate(self) -> float | None:
        """How often it fired on attempts that did not want it. Lower is better.

        **Not ``1 - precision``**: precision divides by the times the skill
        fired, this divides by the times it should not have. A skill that fires
        once wrongly across fifty silent opportunities has 50% precision and a 2%
        unwanted rate, and the second is the honest description of that skill.

        ``None`` when every attempt wanted it, since it then had no opportunity
        to over-fire — which is not the same as never taking one.
        """
        if self.opportunities <= 0:
            return None
        return self.unwanted / self.opportunities


class AggregateScore(BaseModel):
    # Average raw success rate over measured tasks (the primary aggregate).
    avg_score: float
    # How many tasks that average is over. Zero means *nothing was measured* —
    # an all-trigger-probe spec, say — and the headline must then render skipped
    # rather than 0.0%, which would be a fabricated failure of the same kind the
    # activation side is careful to avoid.
    scored_tasks: int = 0
    per_task: list[TaskScore]
    # The activation scoreboard. Kept beside the execution one but never blended
    # into it: a bad `description` and a bad body have opposite fixes, so a
    # single headline mixing them would point at neither (docs/adr/0014).
    # ``None`` when no task asserted `activates:` — rendered skipped, not 0%.
    avg_activation_score: float | None = None
    activation_tasks: int = 0
    activation_asserted: int = 0
    activation_per_skill: list[SkillActivationStats] = Field(default_factory=list)


class RunResults(BaseModel):
    run: RunMeta
    # Plural: a neighbour's `description` is part of what produced the score, so
    # a run is not reproducible without it. Pre-#18 files carrying the singular
    # `skill_snapshot` still load — pydantic ignores the unknown key — and their
    # missing era is what makes `compare` refuse them anyway.
    skill_snapshots: list[SkillSnapshot] = Field(default_factory=list)
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
    as 0). ``regression`` fires on the any-below rule: B below A, both measured.
    """

    task_name: str
    a_score: float | None
    b_score: float | None
    delta: float | None
    regression: bool
    a_outcomes: list[Outcome]
    b_outcomes: list[Outcome]
    # The activation scoreboard's half of the diff, carried alongside and never
    # folded in. Without it the loop that matters most — edit a `description`,
    # re-run, compare — has nothing to read, and a trigger probe (whose
    # execution score is ``None`` by construction) would be a blank row.
    a_activation: float | None = None
    b_activation: float | None = None
    activation_delta: float | None = None
    activation_regression: bool = False


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
    has_regression: bool
    # Strictly separate from ``has_regression``: a description that stopped
    # firing and a body that stopped working have opposite fixes, so one flag
    # covering both would point at neither. A run can regress on activation
    # while execution is flat, and that is the signal a `description` edit
    # needs.
    has_activation_regression: bool = False
    k_mismatch: bool
    spec_mismatch: bool
    # The two runs installed different skill neighbourhoods. A warning, not a
    # refusal: a larger neighbourhood gives the agent competitors and can depress
    # execution scores for reasons unrelated to the skill body, but the result is
    # still legible — unlike a cross-era diff, which is refused outright.
    # ``spec_mismatch`` cannot catch this: it compares the spec *name*.
    neighbourhood_mismatch: bool = False
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
