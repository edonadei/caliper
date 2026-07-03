"""Pure comparison of two saved runs — the ablation reporting primitive.

``diff_runs(a, b)`` is the whole of ``caliper compare``'s logic; the CLI command
and ``--format json`` are thin shells over it. See CONTEXT.md (Run comparison,
Task identity, Regression) for the domain terms.
"""

from __future__ import annotations

from caliper.schema.results import (
    RunComparison,
    RunResults,
    TaskComparison,
    TaskResult,
)


def _group_by_name(results: RunResults) -> dict[str, list[TaskResult]]:
    """Tasks keyed by their stable identity, ``task_name``, preserving order.

    ``task_id`` is only positional (see CONTEXT.md → Task identity), so it is not
    an identity across runs; duplicate names are disambiguated positionally by
    the order they appear here.
    """
    grouped: dict[str, list[TaskResult]] = {}
    for tr in results.task_results:
        grouped.setdefault(tr.task_name, []).append(tr)
    return grouped


def _compare_task(name: str, a: TaskResult, b: TaskResult) -> TaskComparison:
    a_score = a.pass_at_k
    b_score = b.pass_at_k
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


def diff_runs(a: RunResults, b: RunResults) -> RunComparison:
    """Diff two already-saved runs of (nominally) the same eval, A vs B.

    Matches tasks by ``task_name``; tasks present on only one side are surfaced
    as unmatched. The headline aggregate is computed over the *fully-comparable*
    set — tasks measured on both sides — so the delta is strictly like-for-like.
    """
    a_by_name = _group_by_name(a)
    b_by_name = _group_by_name(b)

    matched: list[TaskComparison] = []
    unmatched_a: list[str] = []

    # Walk A's order; pair each name positionally against B's tasks of that name.
    for name, a_tasks in a_by_name.items():
        b_tasks = b_by_name.get(name, [])
        pairs = min(len(a_tasks), len(b_tasks))
        for i in range(pairs):
            matched.append(_compare_task(name, a_tasks[i], b_tasks[i]))
        # A-side tasks with no B counterpart (name absent or fewer in B).
        unmatched_a.extend(name for _ in a_tasks[pairs:])

    # B-side leftovers: names absent from A, plus surplus duplicates of a shared name.
    unmatched_b: list[str] = []
    for name, b_tasks in b_by_name.items():
        matched_count = min(len(a_by_name.get(name, [])), len(b_tasks))
        unmatched_b.extend(name for _ in b_tasks[matched_count:])

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

    spec_mismatch = a.run.spec != b.run.spec
    k_mismatch = a.run.k != b.run.k
    warnings: list[str] = []
    if spec_mismatch:
        warnings.append(
            f"comparing different specs: {a.run.spec} vs {b.run.spec} "
            f"— verify this is intentional"
        )
    if k_mismatch:
        warnings.append(
            f"A ran k={a.run.k}, B ran k={b.run.k} — pass@k not directly comparable"
        )

    return RunComparison(
        a=a.run,
        b=b.run,
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
    )
