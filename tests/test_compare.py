from __future__ import annotations

from datetime import datetime, timezone

from caliper.compare import diff_runs
from caliper.schema.results import (
    AggregateScore,
    AttemptRecord,
    Outcome,
    RunMeta,
    RunResults,
    SkillSnapshot,
    TaskResult,
    TaskScore,
)
from caliper.scoring import pass_at_k, success_rate


def _attempt(i: int, outcome: Outcome) -> AttemptRecord:
    return AttemptRecord(attempt=i, output="", duration_seconds=1.0, outcome=outcome)


def _task(name: str, outcomes: list[Outcome], task_id: str = "task-000") -> TaskResult:
    usable = sum(1 for o in outcomes if o.is_usable)
    successes = sum(1 for o in outcomes if o == Outcome.PASS)
    score = pass_at_k(successes, usable) if usable > 0 else None
    return TaskResult(
        task_id=task_id,
        task_name=name,
        attempts=[_attempt(i + 1, o) for i, o in enumerate(outcomes)],
        successes=successes,
        unusable=len(outcomes) - usable,
        pass_at_k=score,
    )


def _run(tasks: list[TaskResult], *, spec: str = "demo", k: int = 5) -> RunResults:
    per_task = [
        TaskScore(
            task_id=t.task_id,
            task_name=t.task_name,
            k=k,
            successes=t.successes,
            score=t.pass_at_k,
        )
        for t in tasks
    ]
    scored = [t.pass_at_k for t in tasks if t.pass_at_k is not None]
    return RunResults(
        run=RunMeta(
            spec=spec,
            timestamp=datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc),
            k=k,
            backend="claude-code",
        ),
        skill_snapshot=SkillSnapshot(path="/fake/SKILL.md"),
        task_results=tasks,
        aggregate=AggregateScore(
            avg_score=sum(scored) / len(scored) if scored else 0.0,
            per_task=per_task,
        ),
    )


P, F, INF = Outcome.PASS, Outcome.TASK_FAIL, Outcome.INFRA_ERROR


# --------------------------------------------------------------------------
# Identical runs
# --------------------------------------------------------------------------


def test_identical_runs_have_zero_deltas_and_no_regression() -> None:
    def tasks() -> list[TaskResult]:
        return [_task("alpha", [P, P, F, P, F]), _task("beta", [P, P, P, P, P])]

    comp = diff_runs(_run(tasks()), _run(tasks()))

    assert not comp.has_regression
    assert all(tc.delta == 0.0 for tc in comp.matched)
    assert all(not tc.regression for tc in comp.matched)
    assert comp.aggregate_delta == 0.0
    assert comp.unmatched_a == [] and comp.unmatched_b == []


# --------------------------------------------------------------------------
# A single regression
# --------------------------------------------------------------------------


def test_single_task_regression_flags_only_that_task() -> None:
    a = _run([_task("alpha", [P, P, P, P, P]), _task("beta", [P, P, P, P, F])])
    b = _run([_task("alpha", [P, P, P, P, P]), _task("beta", [P, F, F, F, F])])

    comp = diff_runs(a, b)
    by_name = {tc.task_name: tc for tc in comp.matched}

    assert comp.has_regression
    assert not by_name["alpha"].regression
    assert by_name["alpha"].delta == 0.0
    assert by_name["beta"].regression
    assert by_name["beta"].delta is not None and by_name["beta"].delta < 0
    assert comp.aggregate_delta < 0


def test_improvement_is_not_flagged_as_regression() -> None:
    a = _run([_task("alpha", [P, F, F, F, F])])
    b = _run([_task("alpha", [P, P, P, P, F])])

    comp = diff_runs(a, b)
    assert not comp.has_regression
    assert comp.matched[0].delta is not None and comp.matched[0].delta > 0


# --------------------------------------------------------------------------
# Mismatched task sets
# --------------------------------------------------------------------------


def test_unmatched_tasks_are_reported_and_excluded_from_aggregate() -> None:
    a = _run([_task("alpha", [P, P, P, P, P]), _task("only_a", [F, F, F, F, F])])
    b = _run([_task("alpha", [P, P, P, P, P]), _task("only_b", [P, P, P, P, P])])

    comp = diff_runs(a, b)

    assert [tc.task_name for tc in comp.matched] == ["alpha"]
    assert comp.unmatched_a == ["only_a"]
    assert comp.unmatched_b == ["only_b"]
    # Aggregate is over the fully-comparable set (alpha only), not the extras.
    assert comp.a_matched_avg == 1.0
    assert comp.b_matched_avg == 1.0


def test_matching_is_by_name_not_positional_id() -> None:
    # B reorders the tasks; positional ids differ but names must still line up.
    a = _run([_task("alpha", [P, P, P, P, P]), _task("beta", [F, F, F, F, F])])
    b = _run([_task("beta", [F, F, F, F, F]), _task("alpha", [P, P, P, P, P])])

    comp = diff_runs(a, b)
    by_name = {tc.task_name: tc for tc in comp.matched}

    assert set(by_name) == {"alpha", "beta"}
    assert by_name["alpha"].delta == 0.0
    assert by_name["beta"].delta == 0.0
    assert comp.unmatched_a == [] and comp.unmatched_b == []


# --------------------------------------------------------------------------
# Outcome-aware: infra noise must not fake a regression
# --------------------------------------------------------------------------


def test_lower_raw_score_from_unusable_attempts_is_not_a_regression() -> None:
    # A: 4/5 pass. B: same 4 usable passes but its "misses" are infra_errors,
    # excluded from the denominator -> B's raw rate is *higher*, not a regression.
    a = _run([_task("alpha", [P, P, P, P, F])])
    b = _run([_task("alpha", [P, P, P, P, INF])])

    comp = diff_runs(a, b)
    tc = comp.matched[0]
    assert not tc.regression
    assert tc.b_score == success_rate(4, 4) == 1.0  # over 4 usable, not 5
    assert tc.a_score == success_rate(4, 5) == 0.8


def test_fully_unusable_side_is_unmeasured_never_a_regression() -> None:
    a = _run([_task("alpha", [P, P, P, P, F])])
    b = _run([_task("alpha", [INF, INF, INF, INF, INF])])

    comp = diff_runs(a, b)
    tc = comp.matched[0]

    assert tc.b_score is None
    assert tc.delta is None
    assert not tc.regression
    assert not comp.has_regression
    # An unmeasured side is excluded from the comparable aggregate.
    assert comp.a_matched_avg == 0.0 and comp.b_matched_avg == 0.0


# --------------------------------------------------------------------------
# Guards
# --------------------------------------------------------------------------


def test_spec_and_k_mismatch_raise_warnings() -> None:
    a = _run([_task("alpha", [P, P, P, P, P])], spec="full", k=5)
    b = _run([_task("alpha", [P, P, P])], spec="short", k=3)

    comp = diff_runs(a, b)

    assert comp.spec_mismatch
    assert comp.k_mismatch
    assert any("different specs" in w for w in comp.warnings)
    assert any("k=5" in w and "k=3" in w for w in comp.warnings)


def test_matching_specs_and_k_produce_no_warnings() -> None:
    a = _run([_task("alpha", [P, P, P, P, P])])
    b = _run([_task("alpha", [P, P, P, P, P])])

    comp = diff_runs(a, b)
    assert not comp.spec_mismatch
    assert not comp.k_mismatch
    assert comp.warnings == []


# --------------------------------------------------------------------------
# Regression margin (non-inferiority-aware flag)
# --------------------------------------------------------------------------


def _outcomes(n_pass: int, n_fail: int) -> list[Outcome]:
    return [P] * n_pass + [F] * n_fail


def test_margin_zero_preserves_any_below_regression() -> None:
    a = _run([_task("alpha", _outcomes(20, 0), task_id="t1")], k=20)
    b = _run([_task("alpha", _outcomes(19, 1), task_id="t1")], k=20)

    comp = diff_runs(a, b, margin=0.0)

    assert comp.regression_margin == 0.0
    assert comp.has_regression
    assert comp.matched[0].regression


def test_margin_suppresses_small_drop() -> None:
    a = _run([_task("alpha", _outcomes(20, 0), task_id="t1")], k=20)
    b = _run([_task("alpha", _outcomes(19, 1), task_id="t1")], k=20)

    comp = diff_runs(a, b, margin=0.05)

    assert comp.regression_margin == 5.0
    assert not comp.has_regression
    assert not comp.matched[0].regression
    assert comp.matched[0].delta is not None and comp.matched[0].delta < 0


def test_margin_flags_drop_beyond_tolerance() -> None:
    a = _run([_task("alpha", _outcomes(20, 0), task_id="t1")], k=20)
    b = _run([_task("alpha", _outcomes(18, 2), task_id="t1")], k=20)

    comp = diff_runs(a, b, margin=0.05)

    assert comp.regression_margin == 5.0
    assert comp.has_regression
    assert comp.matched[0].regression
