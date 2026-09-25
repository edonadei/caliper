"""Render the report fixtures behind evaluate-skill's diagnosis tasks.

evaluate-skill.eval.yaml hands the agent real `caliper report` / `caliper
compare` output and asks where the fix belongs. The fixtures are rendered from
the reporter, one per branch of the diagnosis table, so they cannot drift from
what caliper prints. Regenerate them whenever the run or compare views change:

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
    RunMeta,
    RunResults,
    SkillSnapshot,
    TaskResult,
    TokenUsage,
)
from caliper.schema.results import RunComparison
from render_readme_samples import _att, _tc, _tokens

P, F, N = Outcome.PASS, Outcome.TASK_FAIL, Outcome.NOT_CHECKED
OUT = Path(__file__).resolve().parent.parent / "skills/evaluate-skill/fixtures"
SKILLS = ["release-notes", "commit-writer"]


def meta(**kw):
    return RunMeta(
        spec="release-notes",
        timestamp=datetime(2026, 9, 20, 10, 0, 0),
        k=3,
        backend="claude-code",
        judge_backend="claude-code",
        era=ERA_INSTALL_AND_DISCOVER,
        **kw,
    )


def results(tasks):
    return RunResults(
        run=meta(),
        skill_snapshots=[
            SkillSnapshot(name="release-notes", path="./SKILL.md"),
            SkillSnapshot(name="commit-writer", path="../commit-writer/SKILL.md"),
        ],
        task_results=tasks,
        aggregate=AggregateScore.from_task_results(tasks, k=3, declared=SKILLS),
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


# The description never fires: the agent writes notes without the skill.
activation = results(
    [
        task(
            "Drafts release notes for v2.2",
            [
                _att(
                    i,
                    F,
                    9.0,
                    tok(),
                    "## v2.2\n- Misc fixes and improvements",
                    assert_evidence="AssertionError: no 'Breaking changes' section",
                    activated=[],
                    activation_passed=False,
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
                    F,
                    8.0,
                    tok(),
                    "Here are the changes since v2.1: ...",
                    assert_evidence="AssertionError: no Features/Fixes headings",
                    activated=[],
                    activation_passed=False,
                )
                for i in (1, 2, 3)
            ],
            ["release-notes"],
        ),
    ]
)

# The skill fires every time, but its output misses what the task checks.
body = results(
    [
        task(
            "Drafts release notes for v2.2",
            [
                _att(
                    1,
                    P,
                    11.0,
                    tok(),
                    "## v2.2\n### Breaking changes\n...",
                    assert_evidence=None,
                    activated=["release-notes"],
                    activation_passed=True,
                ),
                _att(
                    2,
                    F,
                    10.0,
                    tok(),
                    "## v2.2\n### Features\n...",
                    assert_evidence="AssertionError: no 'Breaking changes' section",
                    activated=["release-notes"],
                    activation_passed=True,
                ),
                _att(
                    3,
                    F,
                    12.0,
                    tok(),
                    "## v2.2\n### Fixes\n...",
                    assert_evidence="AssertionError: no 'Breaking changes' section",
                    activated=["release-notes"],
                    activation_passed=True,
                ),
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
    ]
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


# The bare agent already passes: the control matches the full run.
matched = [
    _tc("Drafts release notes for v2.2", 1.0, 1.0, [P] * 3, [P] * 3),
    _tc("Groups changes by type", 2 / 3, 1.0, [P, F, P], [P] * 3),
]
a = sum(t.a_score for t in matched) / 2
b = sum(t.b_score for t in matched) / 2
ua, ub = _tokens(150_000), _tokens(210_000)
ua.wall_seconds = 50.0
ub.wall_seconds = 64.0
cmp = RunComparison(
    a=meta(ablated=["release-notes"]),
    b=RunMeta(
        spec="release-notes",
        timestamp=datetime(2026, 9, 20, 10, 30, 0),
        k=3,
        backend="claude-code",
    ),
    a_label="without release-notes",
    b_label="full neighbourhood",
    matched=matched,
    unmatched_a=[],
    unmatched_b=[],
    a_matched_avg=a,
    b_matched_avg=b,
    aggregate_delta=b - a,
    has_regression=False,
    k_mismatch=False,
    spec_mismatch=False,
    warnings=[],
    a_usage=ua,
    b_usage=ub,
)

render(lambda: print_results(activation), "report-activation-fails.txt")
render(lambda: print_results(body), "report-body-fails.txt")
render(lambda: print_comparison(cmp), "compare-control-matches.txt")
