"""Caliper's scoring module — rolling many tasks up into a run's scoreboards.

One task's own numbers belong to :class:`~caliper.schema.results.TaskResult`,
which derives every one of them from its attempts; this module is what spans
tasks. The metric *formulas* live beside that model (``success_rate`` and
friends), so the usable-only denominator is written once and nothing here
re-derives it — see docs/adr/0007-raw-success-rate-is-the-primary-metric.md and
docs/CONTEXT.md → Usable / unusable attempt.

Two scoreboards, deliberately never merged: the execution rate
(``aggregate_scores``) and activation (``aggregate_activation``).
"""

from __future__ import annotations

from dataclasses import dataclass

from caliper.schema.results import (
    AggregateScore,
    SkillActivationStats,
    TaskResult,
    TaskScore,
)


def aggregate_scores(task_results: list[TaskResult], k: int) -> AggregateScore:
    """The run's execution scoreboard: one row per task, plus their average.

    Takes the results themselves rather than counts pulled out of them — each
    task already knows its own successes, usable denominator and score, and
    re-passing them was an invitation for the two to disagree.

    A task with no usable attempts scores ``None`` and is **excluded** from the
    average rather than dragged to 0%: it was never measured, and averaging a
    non-measurement in would understate the skill (docs/adr/0007).

    ``k`` is the run's requested depth, recorded per row so a reader can see a
    task that ran short of it.
    """
    per_task = [
        TaskScore(
            task_id=task.task_id,
            task_name=task.task_name,
            k=k,
            successes=task.successes,
            score=task.score,
        )
        for task in task_results
    ]

    scored = [t.score for t in per_task if t.score is not None]
    avg = sum(scored) / len(scored) if scored else 0.0
    return AggregateScore(avg_score=avg, scored_tasks=len(scored), per_task=per_task)


@dataclass(frozen=True)
class ActivationAggregate:
    """The activation scoreboard for a run — never merged into the execution one."""

    avg_score: float | None
    # Tasks the average is actually over. Fewer than ``asserted`` when a task
    # asserted but no attempt survived to be measured.
    tasks: int
    asserted: int
    per_skill: list[SkillActivationStats]


@dataclass(frozen=True)
class ObservedActivation:
    """How often one installed skill fired, with nothing asserted about it.

    The counting half of [[activation]] without the scoring half — what an
    ablated run has, since it drops every expectation but keeps every
    observation (docs/adr/0015-ablation-names-its-subject-at-the-invocation.md).
    """

    skill: str
    fired: int
    observed: int


def observed_activations(
    task_results: list[TaskResult], declared: list[str] | None = None
) -> list[ObservedActivation]:
    """Per-skill activation counts over attempts where activation was *observed*.

    Deliberately not ``SkillActivationStats``: with nothing expected, that type's
    recall is undefined and its unwanted rate would read 100% — "fires when not
    wanted" — for a skill that fired exactly when a reader would hope. Nothing
    was wanted because nothing was asserted, which is not the same claim.

    The denominator is ``activation_observed`` (a whole transcript), not
    ``activation_scored`` (which additionally requires an assertion), because an
    ablated run has no assertions left to require.
    """
    fired: dict[str, int] = {}
    observed = 0
    for task in task_results:
        for att in task.attempts:
            if not att.activation_observed:
                continue
            observed += 1
            for name in att.activated or []:
                fired[name] = fired.get(name, 0) + 1

    names = list(declared or [])
    names += sorted(set(fired) - set(names))
    return [
        ObservedActivation(skill=name, fired=fired.get(name, 0), observed=observed)
        for name in names
    ]


def aggregate_activation(
    task_results: list[TaskResult], declared: list[str] | None = None
) -> ActivationAggregate:
    """Roll up the second scoreboard over the tasks that asserted ``activates:``.

    Both halves count **attempts**, not tasks, so the per-skill diagnostic shares
    units with the rate above it. Only activation-usable attempts of asserted
    tasks are counted — an unasserted task contributes nothing and is rendered
    *skipped*, not ``0%``.

    ``declared`` is the whole neighbourhood, in spec order. Every declared skill
    gets a row even if it never fired and was never expected: the reader would
    otherwise have no way to tell what was installed, and a dormant neighbour is
    itself informative — it says the probes never exercised the skill you
    declared *because* you were worried about it.
    """
    scores = [
        t.activation_score for t in task_results if t.activation_score is not None
    ]
    avg = sum(scores) / len(scores) if scores else None

    expected: dict[str, int] = {}
    fired: dict[str, int] = {}
    hits: dict[str, int] = {}
    # Every skill in the neighbourhood is in scope for every scored attempt, so
    # one counter serves them all: it is what the two rates are carved out of.
    considered = 0

    for task in task_results:
        if task.activation_expected is None:
            continue
        wanted = set(task.activation_expected)
        for att in task.attempts:
            if not att.activation_scored:
                continue
            considered += 1
            observed = set(att.activated or [])
            for name in wanted:
                expected[name] = expected.get(name, 0) + 1
            for name in observed:
                fired[name] = fired.get(name, 0) + 1
            for name in wanted & observed:
                hits[name] = hits.get(name, 0) + 1

    # Spec order first (the author's own ordering), then anything observed that
    # was somehow not declared — which the closed neighbourhood should prevent,
    # so it is a safety net rather than an expected case.
    names = list(declared or [])
    names += sorted((set(expected) | set(fired)) - set(names))
    per_skill = [
        SkillActivationStats(
            skill=name,
            total=considered,
            expected=expected.get(name, 0),
            fired=fired.get(name, 0),
            hits=hits.get(name, 0),
        )
        for name in names
    ]
    return ActivationAggregate(
        avg_score=avg,
        tasks=len(scores),
        asserted=sum(1 for t in task_results if t.activation_expected is not None),
        per_skill=per_skill,
    )
