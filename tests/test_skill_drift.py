"""``compare``'s drift report — the same text, or not, across two saved runs.

Drift is graded by *provenance*, not by role: a git source made a
reproducibility claim the spec could keep, a path source made none. See
docs/adr/0017-unpinned-git-sources-are-allowed-because-drift-is-reported.md and
docs/CONTEXT.md → Skill drift.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from caliper.compare import diff_runs
from caliper.schema.results import (
    ERA_INSTALL_AND_DISCOVER,
    AggregateScore,
    AttemptRecord,
    FileSnapshot,
    Outcome,
    RunMeta,
    RunResults,
    SkillSnapshot,
    TaskResult,
)


def _snapshot(
    name: str,
    body: str,
    *,
    source_kind: str = "path",
    git_sha: str | None = None,
) -> SkillSnapshot:
    return SkillSnapshot(
        name=name,
        path=f"/x/{name}/SKILL.md",
        source_kind=source_kind,
        git_sha=git_sha,
        files={"SKILL.md": FileSnapshot(content=body, hash=f"sha256:{body}")},
    )


def _run(snapshots: list[SkillSnapshot], *, offset: int = 0) -> RunResults:
    return RunResults(
        run=RunMeta(
            spec="demo",
            timestamp=datetime(2026, 8, 1, tzinfo=timezone.utc)
            + timedelta(days=offset),
            k=1,
            backend="claude-code",
            era=ERA_INSTALL_AND_DISCOVER,
        ),
        skill_snapshots=snapshots,
        task_results=[
            TaskResult(
                task_id="task-001",
                task_name="alpha",
                attempts=[
                    AttemptRecord(
                        attempt=1, output="", duration_seconds=1.0, outcome=Outcome.PASS
                    )
                ],
                successes=1,
                unusable=0,
                pass_at_k=1.0,
            )
        ],
        aggregate=AggregateScore(total_tasks=1, avg_score=1.0, per_task=[]),
    )


def test_identical_neighbourhoods_report_no_drift():
    a = _run([_snapshot("mine", "v1")])
    b = _run([_snapshot("mine", "v1")], offset=1)
    assert diff_runs(a, b).skill_drift == []


def test_a_drifted_git_source_warns():
    a = _run([_snapshot("tdd", "v1", source_kind="git", git_sha="a" * 40)])
    b = _run([_snapshot("tdd", "v2", source_kind="git", git_sha="b" * 40)], offset=1)

    comp = diff_runs(a, b)

    assert [d.name for d in comp.skill_drift] == ["tdd"]
    assert comp.skill_drift[0].source_kind == "git"
    assert comp.has_skill_drift
    assert any("tdd" in w and "git source" in w for w in comp.warnings)


def test_a_drifted_git_source_reports_the_two_commits():
    a = _run([_snapshot("tdd", "v1", source_kind="git", git_sha="a" * 40)])
    b = _run([_snapshot("tdd", "v2", source_kind="git", git_sha="b" * 40)], offset=1)

    drift = diff_runs(a, b).skill_drift[0]

    assert drift.a_ref == "a" * 7
    assert drift.b_ref == "b" * 7


def test_a_drifted_path_source_is_recorded_but_does_not_warn():
    """The everyday loop: you edited your skill, which is the point of the run."""
    a = _run([_snapshot("mine", "v1")])
    b = _run([_snapshot("mine", "v2")], offset=1)

    comp = diff_runs(a, b)

    assert [d.name for d in comp.skill_drift] == ["mine"]
    assert comp.skill_drift[0].source_kind == "path"
    assert comp.warnings == []


def test_a_path_source_falls_back_to_a_content_digest():
    a = _run([_snapshot("mine", "v1")])
    b = _run([_snapshot("mine", "v2")], offset=1)

    drift = diff_runs(a, b).skill_drift[0]

    assert drift.a_ref != drift.b_ref
    assert len(drift.a_ref) == 7


def test_both_kinds_are_reported_together():
    a = _run(
        [
            _snapshot("mine", "v1"),
            _snapshot("tdd", "v1", source_kind="git", git_sha="a" * 40),
        ]
    )
    b = _run(
        [
            _snapshot("mine", "v2"),
            _snapshot("tdd", "v2", source_kind="git", git_sha="b" * 40),
        ],
        offset=1,
    )

    comp = diff_runs(a, b)

    assert {d.name for d in comp.skill_drift} == {"mine", "tdd"}
    # One warning, for the one member that promised not to move.
    assert len(comp.warnings) == 1
    assert "tdd" in comp.warnings[0]


def test_a_member_present_on_one_side_only_is_not_drift():
    """That is a membership change, which the neighbourhood guard already owns."""
    a = _run([_snapshot("mine", "v1")])
    b = _run([_snapshot("mine", "v1"), _snapshot("tdd", "v1")], offset=1)

    comp = diff_runs(a, b)

    assert comp.skill_drift == []
    assert comp.neighbourhood_mismatch


def test_a_member_that_changed_source_kind_is_graded_as_git():
    """Vendoring a fetched skill (or the reverse) still moved a claimed member."""
    a = _run([_snapshot("tdd", "v1", source_kind="git", git_sha="a" * 40)])
    b = _run([_snapshot("tdd", "v2")], offset=1)

    comp = diff_runs(a, b)

    assert comp.skill_drift[0].source_kind == "git"
    assert comp.warnings


def test_legacy_runs_without_snapshot_files_do_not_report_drift():
    """Pre-#18 snapshots carry no files; an empty digest pair is not evidence."""
    a = _run([SkillSnapshot(name="mine", path="/x/SKILL.md")])
    b = _run([SkillSnapshot(name="mine", path="/x/SKILL.md")], offset=1)
    assert diff_runs(a, b).skill_drift == []
