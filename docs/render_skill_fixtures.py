"""Render the report fixtures behind evaluate-skill's diagnosis tasks.

evaluate-skill.eval.yaml hands the agent real `caliper report` / `caliper
compare` output and asks what it means. Each fixture hides its answer in a line
that needs Caliper-specific reading, because a report that spells the answer
out is one a bare agent reads just as well (a k=1 control showed exactly that).
They are rendered from the reporter, so they cannot drift from what caliper
prints. Regenerate them whenever the run or compare views change:

    uv run python docs/render_skill_fixtures.py
"""

import io
from datetime import datetime
from pathlib import Path

from rich.console import Console

import caliper.reporter as reporter
from caliper.reporter import print_comparison, print_results
from caliper.schema.results import (
    ERA_INSTALL_AND_DISCOVER,
    AggregateScore,
    Outcome,
    RunComparison,
    RunMeta,
    RunResults,
    SkillDriftRecord,
    SkillSnapshot,
    TaskResult,
    TokenUsage,
)
from render_readme_samples import _att, _tc, _tokens

P, F, N = Outcome.PASS, Outcome.TASK_FAIL, Outcome.NOT_CHECKED
OUT = Path(__file__).resolve().parent.parent / "skills/evaluate-skill/fixtures"
SKILLS = ["release-notes", "commit-writer"]


def meta(minute=0, **kw):
    return RunMeta(
        spec="release-notes",
        timestamp=datetime(2026, 9, 20, 10, minute, 0),
        k=3,
        backend="claude-code",
        judge_backend="claude-code",
        era=ERA_INSTALL_AND_DISCOVER,
        **kw,
    )


def results(tasks, run=None):
    run = run or meta()
    declared = [s for s in SKILLS if s not in run.ablated]
    return RunResults(
        run=run,
        skill_snapshots=[
            SkillSnapshot(name="release-notes", path="./SKILL.md"),
            SkillSnapshot(name="commit-writer", path="../commit-writer/SKILL.md"),
        ],
        task_results=tasks,
        aggregate=AggregateScore.from_task_results(tasks, k=3, declared=declared),
    )


def tok():
    return TokenUsage(input_tokens=24_000, output_tokens=400)


def task(name, rows, expected):
    return TaskResult(
        task_id=name.lower().replace(" ", "-"),
        task_name=name,
        attempts=rows,
        activation_expected=expected,
    )


# Every execution task is green, but the silence probe shows release-notes
# firing on unrelated work: the description over-claims.
over_fires = results(
    [
        task(
            "Drafts release notes for v2.2",
            [
                _att(
                    i,
                    P,
                    10.0,
                    tok(),
                    "## v2.2\n### Breaking changes\n...",
                    activated=["release-notes"],
                    activation_passed=True,
                )
                for i in (1, 2, 3)
            ],
            ["release-notes"],
        ),
        task(
            "Groups changes by type",
            [
                _att(
                    i,
                    P,
                    9.0,
                    tok(),
                    "### Features\n...\n### Fixes\n...",
                    activated=["release-notes"],
                    activation_passed=True,
                )
                for i in (1, 2, 3)
            ],
            ["release-notes"],
        ),
        task(
            "Summarizing a meeting fires nothing",
            [
                _att(
                    i,
                    N,
                    4.0,
                    tok(),
                    "Summary of the meeting notes: ...",
                    activated=["release-notes"],
                    activation_passed=False,
                )
                for i in (1, 2, 3)
            ],
            [],
        ),
    ]
)

# The control run: release-notes was removed, so its activation verdict is
# withheld and the scores are the bare agent's. Nothing about the skill broke.
ablated = results(
    [
        task(
            "Drafts release notes for v2.2",
            [
                _att(
                    1,
                    P,
                    9.0,
                    tok(),
                    "## v2.2\n### Breaking changes\n...",
                    activated=[],
                ),
                _att(
                    2,
                    F,
                    9.0,
                    tok(),
                    "## v2.2\n- Misc fixes",
                    assert_evidence="AssertionError: no 'Breaking changes' section",
                    activated=[],
                ),
                _att(
                    3,
                    F,
                    9.0,
                    tok(),
                    "## v2.2\n- Misc fixes",
                    assert_evidence="AssertionError: no 'Breaking changes' section",
                    activated=[],
                ),
            ],
            None,
        ),
    ],
    run=meta(ablated=["release-notes"]),
)


def render(fn, name):
    rec = Console(record=True, width=100, file=io.StringIO(), color_system=None)
    orig = reporter.console
    reporter.console = rec
    try:
        fn()
    finally:
        reporter.console = orig
    (OUT / name).write_text(rec.export_text())


# The user edited release-notes and sees +33%, but the commit-writer neighbour
# is an unpinned git source that also moved between the runs: the delta is
# confounded.
drift = [
    SkillDriftRecord(
        name="commit-writer", source_kind="git", a_ref="a1b2c3d", b_ref="e4f5a6b"
    ),
    SkillDriftRecord(
        name="release-notes", source_kind="path", a_ref="4fc7951", b_ref="bcbcbde"
    ),
]
matched = [
    _tc("Drafts release notes for v2.2", 1 / 3, 1.0, [P, F, F], [P, P, P]),
    _tc("Groups changes by type", 2 / 3, 2 / 3, [P, F, P], [P, P, F]),
    _tc("Leaves commit messages to commit-writer", 1 / 3, 1.0, [F, P, F], [P, P, P]),
]
a_avg = sum(t.a_score for t in matched) / len(matched)
b_avg = sum(t.b_score for t in matched) / len(matched)
ua, ub = _tokens(210_000), _tokens(205_000)
ua.wall_seconds, ub.wall_seconds = 70.0, 66.0
confounded = RunComparison(
    a=meta(),
    b=meta(minute=45),
    matched=matched,
    unmatched_a=[],
    unmatched_b=[],
    a_matched_avg=a_avg,
    b_matched_avg=b_avg,
    aggregate_delta=b_avg - a_avg,
    has_regression=False,
    k_mismatch=False,
    spec_mismatch=False,
    warnings=[d.message for d in drift if d.source_kind == "git"],
    skill_drift=drift,
    a_usage=ua,
    b_usage=ub,
)

for old in OUT.glob("*.txt"):
    old.unlink()
render(lambda: print_results(over_fires), "report-over-fires.txt")
render(lambda: print_results(ablated), "report-ablated-run.txt")
render(lambda: print_comparison(confounded), "compare-drift-confounded.txt")
