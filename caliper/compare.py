"""Pure comparison of two runs — the ablation reporting primitive.

``diff_runs(a, b)`` is the whole of ``caliper compare``'s logic; the CLI command
and ``--format json`` are thin shells over it. ``diff_baseline`` reuses the exact
same machinery for a ``--baseline`` run (no skill vs with skill), so there is one
comparison path, not two. See docs/CONTEXT.md (Run comparison, Task identity,
Regression) for the domain terms.
"""

from __future__ import annotations

from caliper.schema.results import (
    RunComparison,
    RunMeta,
    RunResults,
    TaskComparison,
    TaskResult,
    UsageTotals,
)


def _group_by_name(tasks: list[TaskResult]) -> dict[str, list[TaskResult]]:
    """Tasks keyed by their stable identity, ``task_name``, preserving order.

    ``task_id`` is only positional (see docs/CONTEXT.md → Task identity), so it is not
    an identity across runs; duplicate names are disambiguated positionally by
    the order they appear here.
    """
    grouped: dict[str, list[TaskResult]] = {}
    for tr in tasks:
        grouped.setdefault(tr.task_name, []).append(tr)
    return grouped


def _compare_task(name: str, a: TaskResult, b: TaskResult) -> TaskComparison:
    a_score = a.score
    b_score = b.score
    both_measured = a_score is not None and b_score is not None
    return TaskComparison(
        task_name=name,
        a_score=a_score,
        b_score=b_score,
        delta=(b_score - a_score) if both_measured else None,
        # Any-below rule; an unmeasured side is unknown, never a regression.
        regression=both_measured and b_score < a_score,
        a_outcomes=[att.outcome for att in a.attempts],
        b_outcomes=[att.outcome for att in b.attempts],
    )


def diff_runs(
    a: RunResults,
    b: RunResults,
    *,
    a_label: str | None = None,
    b_label: str | None = None,
) -> RunComparison:
    """Diff two already-saved runs of (nominally) the same eval, A vs B.

    Matches tasks by ``task_name``; tasks present on only one side are surfaced
    as unmatched. The headline aggregate is computed over the *fully-comparable*
    set — tasks measured on both sides — so the delta is strictly like-for-like.
    """
    return _diff(
        a.run,
        a.task_results,
        b.run,
        b.task_results,
        a_label=a_label,
        b_label=b_label,
    )


def diff_baseline(results: RunResults) -> RunComparison:
    """The no-skill-vs-with-skill diff of a ``--baseline`` run.

    Both sides share the run's single ``RunMeta`` (same spec, k, engine), so it
    routes through the same ``_diff`` as ``compare`` and just labels the sides.
    Assumes ``results.baseline_task_results`` is present (the caller checks).
    """
    assert results.baseline_task_results is not None
    return _diff(
        results.run,
        results.baseline_task_results,
        results.run,
        results.task_results,
        a_label="no skill",
        b_label="with skill",
    )


def _diff(
    a_run: RunMeta,
    a_tasks: list[TaskResult],
    b_run: RunMeta,
    b_tasks: list[TaskResult],
    *,
    a_label: str | None,
    b_label: str | None,
) -> RunComparison:
    a_by_name = _group_by_name(a_tasks)
    b_by_name = _group_by_name(b_tasks)

    matched: list[TaskComparison] = []
    unmatched_a: list[str] = []

    # Walk A's order; pair each name positionally against B's tasks of that name.
    for name, a_group in a_by_name.items():
        b_group = b_by_name.get(name, [])
        pairs = min(len(a_group), len(b_group))
        for i in range(pairs):
            matched.append(_compare_task(name, a_group[i], b_group[i]))
        # A-side tasks with no B counterpart (name absent or fewer in B).
        unmatched_a.extend(name for _ in a_group[pairs:])

    # B-side leftovers: names absent from A, plus surplus duplicates of a shared name.
    unmatched_b: list[str] = []
    for name, b_group in b_by_name.items():
        matched_count = min(len(a_by_name.get(name, [])), len(b_group))
        unmatched_b.extend(name for _ in b_group[matched_count:])

    # Headline aggregate over tasks measured on both sides only.
    comparable = [
        tc for tc in matched if tc.a_score is not None and tc.b_score is not None
    ]
    a_avg = (
        sum(tc.a_score for tc in comparable) / len(comparable) if comparable else 0.0
    )
    b_avg = (
        sum(tc.b_score for tc in comparable) / len(comparable) if comparable else 0.0
    )

    spec_mismatch = a_run.spec != b_run.spec
    k_mismatch = a_run.k != b_run.k
    warnings: list[str] = []
    if spec_mismatch:
        warnings.append(
            f"comparing different specs: {a_run.spec} vs {b_run.spec} "
            f"— verify this is intentional"
        )
    if k_mismatch:
        warnings.append(
            f"A ran k={a_run.k}, B ran k={b_run.k} — pass@k not directly comparable"
        )

    return RunComparison(
        a=a_run,
        b=b_run,
        a_label=a_label,
        b_label=b_label,
        matched=matched,
        unmatched_a=unmatched_a,
        unmatched_b=unmatched_b,
        a_matched_avg=a_avg,
        b_matched_avg=b_avg,
        aggregate_delta=b_avg - a_avg,
        has_regression=any(tc.regression for tc in matched),
        k_mismatch=k_mismatch,
        spec_mismatch=spec_mismatch,
        warnings=warnings,
        # Token/wall totals over each whole run. Shown alongside pass@k but never
        # folded into has_regression — a token drop is a win, not a regression.
        a_usage=UsageTotals.from_task_results(a_tasks),
        b_usage=UsageTotals.from_task_results(b_tasks),
    )
