"""Pure comparison of two runs — the ablation reporting primitive.

``diff_runs(a, b)`` is the whole of ``caliper compare``'s logic; the CLI command
and ``--format json`` are thin shells over it. There is no within-run diff: an
ablated arm is an ordinary saved run, so skill-vs-no-skill and
candidate-vs-control travel this one path rather than two. See docs/CONTEXT.md
(Run comparison, Ablation, Task identity, Regression) for the domain terms.
"""

from __future__ import annotations

from caliper.schema.results import (
    RunComparison,
    RunMeta,
    RunResults,
    SkillDriftRecord,
    SkillSnapshot,
    TaskComparison,
    TaskResult,
    UsageTotals,
)


class IncomparableRunsError(ValueError):
    """Two runs cannot be diffed at all — refused rather than warned about."""


def _neighbourhood(results: RunResults) -> list[str]:
    """The names of the skills a run installed, as its comparability key."""
    return sorted(s.name for s in results.skill_snapshots if s.name)


def _drift(a: RunResults, b: RunResults) -> list[SkillDriftRecord]:
    """Members installed by both runs whose captured text differs.

    The snapshots have carried per-file hashes since they went plural and
    nothing has ever read them — ``_neighbourhood`` compares only names, so
    today a change of *membership* is caught and a change of *text* is not.

    Matched on name, the same stable identity everything else uses. A member on
    one side only is deliberately not drift: that is a membership change, and
    ``neighbourhood_mismatch`` already owns it.
    """
    b_by_name = {s.name: s for s in b.skill_snapshots if s.name}
    records: list[SkillDriftRecord] = []

    for a_snap in a.skill_snapshots:
        b_snap = b_by_name.get(a_snap.name)
        if b_snap is None or not a_snap.name:
            continue
        # A legacy snapshot captured no files, so both digests are the digest of
        # nothing. Equal, and no claim either way — which is the right answer.
        if not a_snap.files and not b_snap.files:
            continue
        if a_snap.content_digest == b_snap.content_digest:
            continue
        # Graded git if *either* side was fetched: vendoring a fetched skill (or
        # the reverse) still moved a member that had been claimed.
        kind = "git" if "git" in (a_snap.source_kind, b_snap.source_kind) else "path"
        records.append(
            SkillDriftRecord(
                name=a_snap.name,
                source_kind=kind,
                a_ref=_short_ref(a_snap),
                b_ref=_short_ref(b_snap),
            )
        )
    return records


def _short_ref(snap: SkillSnapshot) -> str:
    """A seven-character handle for what this member *was* on one side.

    The resolved commit when there is one, because that is the thing an author
    can put in `ref:` to hold it still; otherwise a digest over the captured
    files, which identifies the text without pretending to be a commit.
    """
    return (snap.git_sha or snap.content_digest)[:7]


def _check_era(a: RunMeta, b: RunMeta) -> None:
    """Refuse a cross-era diff. The one guard that stops rather than warns.

    A ``k_mismatch`` or a neighbourhood change yields a diff that is confounded
    but *legible* — a human reads the warning and reasons about it. A cross-era
    diff yields a number that is meaningless and looks entirely normal: same
    spec, same k, same task names, a plausible delta. Nothing in the output
    invites suspicion, which is exactly what earns a hard stop.
    """
    if a.era == b.era:
        return
    labels = {None: "pre-install-and-discover (force-loaded)"}
    raise IncomparableRunsError(
        "These runs were produced under different loading disciplines and their "
        "numbers are not comparable:\n"
        f"  A: {labels.get(a.era, a.era)}\n"
        f"  B: {labels.get(b.era, b.era)}\n\n"
        "Before install-and-discover, claude-code measured invocation x "
        "execution under a mangled skill name while the other backends measured "
        "execution with the skill force-loaded — neither is what a run measures "
        "now. Re-run the older side to compare."
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
    # The two scoreboards are diffed by the same rule but never mixed: each has
    # its own delta and its own regression flag.
    a_act, b_act = a.activation_score, b.activation_score
    both_activation = a_act is not None and b_act is not None
    return TaskComparison(
        task_name=name,
        a_score=a_score,
        b_score=b_score,
        delta=(b_score - a_score) if both_measured else None,
        # Any-below rule; an unmeasured side is unknown, never a regression.
        regression=both_measured and b_score < a_score,
        a_outcomes=[att.outcome for att in a.attempts],
        b_outcomes=[att.outcome for att in b.attempts],
        a_activation=a_act,
        b_activation=b_act,
        activation_delta=(b_act - a_act) if both_activation else None,
        activation_regression=both_activation and b_act < a_act,
    )


def _ablation_labels(
    a_run: RunMeta,
    a_neighbourhood: list[str],
    b_run: RunMeta,
    b_neighbourhood: list[str],
) -> tuple[str, str] | None:
    """Side labels when these two runs form an ablation pair, else ``None``.

    A pair is exactly one ablated side against one full side, where the ablated
    side's neighbourhood really is the other's minus what it says it removed.
    The marker is *checked*, not merely trusted: a run whose ``ablated`` claim
    disagrees with its own snapshots falls back to the generic warning.

    Two runs that ablated *different* skills are deliberately **not** a pair —
    nothing but this marker could tell that case apart from a legitimate one,
    since both sides simply have a smaller-than-declared neighbourhood.
    """
    if bool(a_run.ablated) == bool(b_run.ablated):
        return None
    if a_run.ablated:
        cut, cut_nb, full_nb = a_run.ablated, a_neighbourhood, b_neighbourhood
    else:
        cut, cut_nb, full_nb = b_run.ablated, b_neighbourhood, a_neighbourhood
    if set(cut_nb) != set(full_nb) - set(cut):
        return None
    cut_label = "bare agent" if not cut_nb else f"without {', '.join(sorted(cut))}"
    return (
        (cut_label, "full neighbourhood")
        if a_run.ablated
        else ("full neighbourhood", cut_label)
    )


def diff_runs(a: RunResults, b: RunResults) -> RunComparison:
    """Diff two already-saved runs of (nominally) the same eval, A vs B.

    Matches tasks by ``task_name``; tasks present on only one side are surfaced
    as unmatched. The headline aggregate is computed over the *fully-comparable*
    set — tasks measured on both sides — so the delta is strictly like-for-like.

    Raises ``IncomparableRunsError`` when the two runs come from different eras.
    """
    _check_era(a.run, b.run)
    a_run, b_run = a.run, b.run
    a_tasks, b_tasks = a.task_results, b.task_results
    a_neighbourhood, b_neighbourhood = _neighbourhood(a), _neighbourhood(b)

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
    # On a recognised ablation pair the differing neighbourhood *is* the
    # experiment, so the generic warning would be describing the design as a
    # mistake — and the sides get titled from the marker instead.
    labels = _ablation_labels(a_run, a_neighbourhood, b_run, b_neighbourhood)
    a_label, b_label = labels if labels else (None, None)
    neighbourhood_mismatch = labels is None and a_neighbourhood != b_neighbourhood
    if neighbourhood_mismatch:
        warnings.append(
            f"different skill neighbourhoods: {a_neighbourhood or ['(none)']} vs "
            f"{b_neighbourhood or ['(none)']} — the larger set gives the agent "
            "competitors, so some attempts may never activate the skill at all"
        )

    # Drift is reported for every member but only *warned* about for a git
    # source. Warning on a path source would fire on every iteration of the core
    # loop — you edited your skill, which is what the run is measuring — and a
    # warning trained past is worse than none, because it takes the confounding
    # case down with it. See docs/adr/0017.
    skill_drift = _drift(a, b)
    for record in skill_drift:
        if record.source_kind != "git":
            continue
        warnings.append(
            f"{record.name} changed between runs — git source, "
            f"{record.a_ref} → {record.b_ref}; pin `ref:` to hold it fixed"
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
        has_activation_regression=any(tc.activation_regression for tc in matched),
        k_mismatch=k_mismatch,
        spec_mismatch=spec_mismatch,
        neighbourhood_mismatch=neighbourhood_mismatch,
        skill_drift=skill_drift,
        warnings=warnings,
        # Token/wall totals over each whole run. Shown alongside pass@k but never
        # folded into has_regression — a token drop is a win, not a regression.
        a_usage=UsageTotals.from_task_results(a_tasks),
        b_usage=UsageTotals.from_task_results(b_tasks),
    )
