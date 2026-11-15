"""Laws of ``compare``, over generated pairs of runs.

Swapping the sides negates every delta and mirrors every list; reordering B's
tasks changes nothing. See docs/CONTEXT.md → Run comparison and Skill drift.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import datetime, timezone

from hypothesis import given
from hypothesis import strategies as st

from caliper.compare import diff_runs
from caliper.schema.results import (
    AggregateScore,
    AttemptRecord,
    FileSnapshot,
    Outcome,
    RunComparison,
    RunMeta,
    RunResults,
    SkillSnapshot,
    TaskResult,
)

NAMES = ["alpha", "beta", "gamma", "delta"]

attempt_outcomes = st.lists(st.sampled_from(Outcome), min_size=1, max_size=5)
# Names may repeat within a run: compare pairs a repeated name positionally.
tasks = st.lists(st.tuples(st.sampled_from(NAMES), attempt_outcomes), max_size=6)
unique_tasks = st.lists(
    st.tuples(st.sampled_from(NAMES), attempt_outcomes),
    max_size=len(NAMES),
    unique_by=lambda task: task[0],
)
snapshots = st.lists(
    st.tuples(
        st.sampled_from(NAMES),
        st.sampled_from(["path", "git"]),
        st.sampled_from(["h1", "h2", "h3"]),
    ),
    max_size=4,
    unique_by=lambda snapshot: snapshot[0],
)


def _run(
    task_specs: Sequence[tuple[str, list[Outcome]]],
    snapshot_specs: Sequence[tuple[str, str, str]] = (),
    ablated: Sequence[str] = (),
) -> RunResults:
    task_results = [
        TaskResult(
            task_id=f"task-{i:03d}",
            task_name=name,
            attempts=[
                AttemptRecord(
                    attempt=n, output="", duration_seconds=1.0, outcome=outcome
                )
                for n, outcome in enumerate(outcomes, start=1)
            ],
        )
        for i, (name, outcomes) in enumerate(task_specs)
    ]
    return RunResults(
        run=RunMeta(
            spec="demo",
            timestamp=datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc),
            k=5,
            backend="claude-code",
            ablated=list(ablated),
        ),
        skill_snapshots=[
            SkillSnapshot(
                name=name,
                path=f"/skills/{name}/SKILL.md",
                source_kind=kind,
                files={"SKILL.md": FileSnapshot(hash=digest)},
            )
            for name, kind, digest in snapshot_specs
        ],
        task_results=task_results,
        aggregate=AggregateScore.from_task_results(task_results, k=5),
    )


def _deltas(comparison: RunComparison) -> dict[str, list[float | None]]:
    """Each name's deltas, in pairing order: a repeated name pairs positionally."""
    deltas: dict[str, list[float | None]] = {}
    for tc in comparison.matched:
        deltas.setdefault(tc.task_name, []).append(tc.delta)
    return deltas


@given(tasks, tasks)
def test_swapping_the_sides_negates_every_delta(a_tasks, b_tasks) -> None:
    forward = diff_runs(_run(a_tasks), _run(b_tasks))
    backward = diff_runs(_run(b_tasks), _run(a_tasks))

    # Swapping reorders each side's sum, so equal only to float precision.
    assert math.isclose(
        backward.aggregate_delta, -forward.aggregate_delta, abs_tol=1e-12
    )
    assert _deltas(backward) == {
        name: [None if delta is None else -delta for delta in deltas]
        for name, deltas in _deltas(forward).items()
    }


@given(unique_tasks, unique_tasks)
def test_a_regression_one_way_is_never_one_the_other(a_tasks, b_tasks) -> None:
    forward = diff_runs(_run(a_tasks), _run(b_tasks))
    backward = diff_runs(_run(b_tasks), _run(a_tasks))
    assert set(forward.regressions).isdisjoint(backward.regressions)


@given(tasks, tasks)
def test_swapping_the_sides_mirrors_the_unmatched_tasks(a_tasks, b_tasks) -> None:
    forward = diff_runs(_run(a_tasks), _run(b_tasks))
    backward = diff_runs(_run(b_tasks), _run(a_tasks))
    assert forward.unmatched_a == backward.unmatched_b
    assert forward.unmatched_b == backward.unmatched_a


@given(snapshots, snapshots)
def test_swapping_the_sides_swaps_each_drift_record(a_skills, b_skills) -> None:
    forward = diff_runs(_run([], a_skills), _run([], b_skills)).skill_drift
    backward = diff_runs(_run([], b_skills), _run([], a_skills)).skill_drift
    assert {r.name: (r.source_kind, r.a_ref, r.b_ref) for r in forward} == {
        r.name: (r.source_kind, r.b_ref, r.a_ref) for r in backward
    }


@given(unique_tasks, unique_tasks, st.randoms(use_true_random=False))
def test_reordering_b_changes_nothing(a_tasks, b_tasks, random) -> None:
    shuffled = list(b_tasks)
    random.shuffle(shuffled)
    original = diff_runs(_run(a_tasks), _run(b_tasks))
    reordered = diff_runs(_run(a_tasks), _run(shuffled))

    assert _deltas(reordered) == _deltas(original)
    assert reordered.aggregate_delta == original.aggregate_delta
    assert sorted(reordered.unmatched_b) == sorted(original.unmatched_b)


skill_sets = st.frozensets(st.sampled_from(NAMES))


def _installed(names: frozenset[str]) -> list[tuple[str, str, str]]:
    return [(name, "path", "h1") for name in sorted(names)]


@st.composite
def ablation_sides(draw) -> tuple[frozenset[str], frozenset[str], frozenset[str]]:
    """A full side, the subjects removed, and an ablated side that is, about
    half the time, exactly the full side minus them."""
    full = draw(skill_sets)
    removed = draw(skill_sets.filter(bool))
    cut = draw(st.just(full - removed) | skill_sets)
    return full, removed, cut


@given(ablation_sides(), st.booleans())
def test_an_ablation_pair_is_exactly_the_full_side_minus_its_subjects(
    sides, cut_first
) -> None:
    full, removed, cut = sides
    full_run = _run([], _installed(full))
    cut_run = _run([], _installed(cut), ablated=sorted(removed))
    a, b = (cut_run, full_run) if cut_first else (full_run, cut_run)
    comparison = diff_runs(a, b)
    full_label = comparison.b_label if cut_first else comparison.a_label

    if removed <= full and cut == full - removed:
        assert full_label == f"with {', '.join(sorted(removed))}"
    else:
        assert (comparison.a_label, comparison.b_label) == (None, None)


@given(ablation_sides())
def test_swapping_an_ablation_pair_swaps_its_labels(sides) -> None:
    full, removed, cut = sides
    full_run = _run([], _installed(full))
    cut_run = _run([], _installed(cut), ablated=sorted(removed))
    forward = diff_runs(cut_run, full_run)
    backward = diff_runs(full_run, cut_run)
    assert (forward.a_label, forward.b_label) == (backward.b_label, backward.a_label)
