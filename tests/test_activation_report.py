from __future__ import annotations

from datetime import datetime, timezone

import pytest
from rich.console import Console

from caliper.compare import IncomparableRunsError, diff_runs
from caliper.reporter import _activation_cell, print_results
from caliper.schema.results import (
    ERA_INSTALL_AND_DISCOVER,
    AggregateScore,
    AttemptRecord,
    Outcome,
    RunMeta,
    RunResults,
    SkillActivationStats,
    SkillSnapshot,
    TaskResult,
)


def attempt(n, outcome=Outcome.PASS, activated=None, activation_passed=None):
    return AttemptRecord(
        attempt=n,
        output="",
        duration_seconds=1.0,
        outcome=outcome,
        activated=activated,
        activation_passed=activation_passed,
    )


def task(attempts, expected=None, name="t"):
    return TaskResult(
        task_id="task-001",
        task_name=name,
        attempts=attempts,
        successes=sum(1 for a in attempts if a.outcome == Outcome.PASS),
        unusable=sum(1 for a in attempts if not a.outcome.is_usable),
        pass_at_k=None,
        activation_expected=expected,
    )


def render(results: RunResults) -> str:
    console = Console(width=200, force_terminal=False)
    with console.capture() as cap:
        import caliper.reporter as reporter

        original, reporter.console = reporter.console, console
        try:
            print_results(results)
        finally:
            reporter.console = original
    return cap.get()


# --- the activated column -------------------------------------------------


def test_column_counts_each_skill_over_activation_usable_attempts():
    t = task(
        [
            attempt(1, activated=["mine"], activation_passed=True),
            attempt(2, activated=[], activation_passed=False),
            attempt(3, activated=["mine"], activation_passed=True),
        ],
        expected=["mine"],
    )
    assert _activation_cell(t).plain == "mine 2/3"


def test_column_says_none_when_the_agent_reached_for_nothing():
    t = task([attempt(1, activated=[], activation_passed=False)], expected=["mine"])
    assert _activation_cell(t).plain == "(none) 1/1"


def test_a_fully_timed_out_task_shows_no_claim_about_the_skill():
    # `(none) 5/5` here would be a confident "the description never fires",
    # manufactured from an infrastructure failure.
    t = task(
        [
            attempt(n, Outcome.TIMEOUT, activated=[], activation_passed=False)
            for n in (1, 2)
        ],
        expected=["mine"],
    )
    assert _activation_cell(t).plain == "—"


def test_column_is_dim_when_nothing_was_asserted():
    t = task([attempt(1, activated=["mine"])], expected=None)
    assert _activation_cell(t).style == "dim"


def test_column_is_green_when_the_assertion_held():
    t = task(
        [attempt(1, activated=["mine"], activation_passed=True)], expected=["mine"]
    )
    assert _activation_cell(t).style == "green"


def test_column_is_red_when_a_neighbour_hijacked():
    t = task(
        [attempt(1, activated=["other"], activation_passed=False)],
        expected=["mine"],
    )
    cell = _activation_cell(t)
    assert cell.style == "red"
    assert cell.plain == "other 1/1"


def test_unobserved_activation_renders_as_a_dash():
    t = task([attempt(1, activated=None)], expected=None)
    assert _activation_cell(t).plain == "—"


# --- the aggregate block --------------------------------------------------


def _results(tasks, aggregate) -> RunResults:
    return RunResults(
        run=RunMeta(
            spec="demo",
            timestamp=datetime(2026, 8, 1, tzinfo=timezone.utc),
            k=3,
            backend="claude-code",
            era=ERA_INSTALL_AND_DISCOVER,
        ),
        skill_snapshots=[SkillSnapshot(name="mine", path="/x/SKILL.md")],
        task_results=tasks,
        aggregate=aggregate,
    )


def test_report_prints_both_scoreboards_separately():
    tasks = [task([attempt(1, activated=["mine"], activation_passed=True)], ["mine"])]
    out = render(
        _results(
            tasks,
            AggregateScore(
                avg_score=1.0,
                per_task=[],
                avg_activation_score=0.733,
                activation_tasks=3,
                activation_per_skill=[
                    SkillActivationStats(skill="mine", expected=2, fired=4, hits=2)
                ],
            ),
        )
    )
    assert "Execution" in out
    assert "Activation" in out
    assert "73.3%" in out
    assert "3 asserted tasks" in out
    # Per-skill diagnostic: recall 2/2, precision 2/4.
    assert "mine" in out
    assert "100.0%" in out
    assert "50.0%" in out


def test_activation_line_is_absent_when_nothing_was_asserted():
    tasks = [task([attempt(1, activated=["mine"])], None)]
    out = render(_results(tasks, AggregateScore(avg_score=1.0, per_task=[])))
    assert "Execution" in out
    assert "Activation" not in out


# --- compare guards -------------------------------------------------------


def _run(*, era, skills, spec="demo") -> RunResults:
    return RunResults(
        run=RunMeta(
            spec=spec,
            timestamp=datetime(2026, 8, 1, tzinfo=timezone.utc),
            k=3,
            backend="claude-code",
            era=era,
        ),
        skill_snapshots=[
            SkillSnapshot(name=n, path=f"/x/{n}/SKILL.md") for n in skills
        ],
        task_results=[task([attempt(1)], None, name="shared")],
        aggregate=AggregateScore(avg_score=1.0, per_task=[]),
    )


def test_compare_refuses_a_cross_era_diff():
    new = _run(era=ERA_INSTALL_AND_DISCOVER, skills=["mine"])
    legacy = _run(era=None, skills=[])
    with pytest.raises(IncomparableRunsError) as exc:
        diff_runs(legacy, new)
    assert "force-loaded" in str(exc.value)


def test_compare_allows_two_runs_of_the_same_era():
    a = _run(era=ERA_INSTALL_AND_DISCOVER, skills=["mine"])
    b = _run(era=ERA_INSTALL_AND_DISCOVER, skills=["mine"])
    comp = diff_runs(a, b)
    assert comp.neighbourhood_mismatch is False
    assert comp.warnings == []


def test_compare_warns_but_still_renders_on_a_neighbourhood_change():
    # Legible-but-confounded: warn, don't refuse.
    a = _run(era=ERA_INSTALL_AND_DISCOVER, skills=["mine"])
    b = _run(era=ERA_INSTALL_AND_DISCOVER, skills=["mine", "rival"])
    comp = diff_runs(a, b)
    assert comp.neighbourhood_mismatch is True
    assert any("neighbourhood" in w for w in comp.warnings)
    assert len(comp.matched) == 1


def test_two_legacy_runs_still_compare():
    # The guard is on the boundary, not on being old.
    comp = diff_runs(_run(era=None, skills=[]), _run(era=None, skills=[]))
    assert len(comp.matched) == 1


# --- activation delta in compare ------------------------------------------


def _act_task(name, expected, passed_flags):
    return TaskResult(
        task_id="task-001",
        task_name=name,
        attempts=[
            AttemptRecord(
                attempt=i + 1,
                output="",
                duration_seconds=1.0,
                outcome=Outcome.NOT_CHECKED,
                activated=["mine"] if p else [],
                activation_passed=p,
            )
            for i, p in enumerate(passed_flags)
        ],
        successes=0,
        unusable=0,
        pass_at_k=None,
        activation_expected=expected,
    )


def _act_run(passed_flags):
    return RunResults(
        run=RunMeta(
            spec="demo",
            timestamp=datetime(2026, 8, 1, tzinfo=timezone.utc),
            k=len(passed_flags),
            backend="claude-code",
            era=ERA_INSTALL_AND_DISCOVER,
        ),
        skill_snapshots=[SkillSnapshot(name="mine", path="/x/SKILL.md")],
        task_results=[_act_task("canonical ask", ["mine"], passed_flags)],
        aggregate=AggregateScore(avg_score=0.0, per_task=[]),
    )


def test_compare_surfaces_an_activation_delta_for_a_trigger_only_task():
    # Execution score is None on both sides; without the activation half this
    # row would be blank — the description edit would be invisible.
    comp = diff_runs(
        _act_run([True, True, True, True]), _act_run([True, False, False, False])
    )
    tc = comp.matched[0]
    assert tc.a_score is None and tc.b_score is None
    assert tc.a_activation == 1.0
    assert tc.b_activation == 0.25
    assert tc.activation_delta == -0.75
    assert tc.activation_regression is True


def test_activation_regression_is_separate_from_execution_regression():
    comp = diff_runs(_act_run([True, True]), _act_run([False, False]))
    assert comp.has_activation_regression is True
    # Execution never moved — flagging it would point at the body, not the
    # description that actually broke.
    assert comp.has_regression is False


def test_no_activation_regression_when_the_description_improves():
    comp = diff_runs(_act_run([False, False]), _act_run([True, True]))
    tc = comp.matched[0]
    assert tc.activation_delta == 1.0
    assert tc.activation_regression is False
    assert comp.has_activation_regression is False


def test_execution_headline_is_skipped_when_nothing_was_measured():
    # An all-trigger-probe spec. "0.0%" with an empty bar would read as total
    # failure of a run in which nothing failed.
    tasks = [
        TaskResult(
            task_id="task-001",
            task_name="silence",
            attempts=[
                AttemptRecord(
                    attempt=1,
                    output="",
                    duration_seconds=1.0,
                    outcome=Outcome.NOT_CHECKED,
                    activated=[],
                    activation_passed=True,
                )
            ],
            successes=0,
            unusable=0,
            pass_at_k=None,
            activation_expected=[],
        )
    ]
    out = render(
        _results(
            tasks,
            AggregateScore(
                avg_score=0.0,
                scored_tasks=0,
                per_task=[],
                avg_activation_score=1.0,
                activation_tasks=1,
            ),
        )
    )
    execution_line = next(ln for ln in out.splitlines() if "Execution" in ln)
    assert "%" not in execution_line
    assert "no execution checks" in execution_line
    # The activation scoreboard is unaffected and still reports.
    assert "Activation" in out


def test_execution_headline_reports_its_task_count():
    tasks = [task([attempt(1, activated=["mine"], activation_passed=True)], ["mine"])]
    out = render(
        _results(tasks, AggregateScore(avg_score=1.0, scored_tasks=2, per_task=[]))
    )
    assert "2 tasks" in out
