from __future__ import annotations

import re
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


# --- the activation column ------------------------------------------------


def _style_of(cell, fragment: str) -> str:
    """The style a cell gives the first occurrence of ``fragment``."""
    start = cell.plain.index(fragment)
    styles = [str(s.style) for s in cell.spans if s.start <= start < s.end]
    return " ".join(styles) or str(cell.style)


def test_column_is_a_verdict_per_attempt_not_a_list_of_names():
    t = task(
        [
            attempt(1, activated=["mine"], activation_passed=True),
            attempt(2, activated=[], activation_passed=False),
            attempt(3, activated=["mine"], activation_passed=True),
        ],
        expected=["mine"],
    )
    # Names and counts live on the per-skill table; this is the rate and one
    # verdict per attempt.
    cell = _activation_cell(t, k=3)
    assert cell.plain.split() == ["66.7%", "✓", "✗", "✓"]
    assert "mine" not in cell.plain


def test_column_fails_when_the_agent_reached_for_nothing():
    t = task([attempt(1, activated=[], activation_passed=False)], expected=["mine"])
    cell = _activation_cell(t, k=1)
    assert cell.plain.split() == ["0%", "✗"]
    assert "red" in _style_of(cell, "0%")


def test_a_fully_timed_out_task_shows_no_claim_about_the_skill():
    # "0%" here would be a confident "the description never fires",
    # manufactured from an infrastructure failure.
    t = task(
        [
            attempt(n, Outcome.TIMEOUT, activated=[], activation_passed=False)
            for n in (1, 2)
        ],
        expected=["mine"],
    )
    assert _activation_cell(t, k=2).plain.split() == ["—", "⊘", "⊘"]


def test_column_is_dim_when_nothing_was_asserted():
    t = task([attempt(1, activated=["mine"])], expected=None)
    cell = _activation_cell(t, k=1)
    assert cell.plain.strip() == "—"
    assert cell.style == "dim"


def test_a_held_assertion_is_marked_green():
    t = task(
        [attempt(1, activated=["mine"], activation_passed=True)], expected=["mine"]
    )
    cell = _activation_cell(t, k=1)
    assert "red" not in _style_of(cell, "100%")
    assert "green" in _style_of(cell, "✓")


def test_column_is_red_when_a_neighbour_hijacked():
    t = task(
        [attempt(1, activated=["other"], activation_passed=False)],
        expected=["mine"],
    )
    cell = _activation_cell(t, k=1)
    assert cell.plain.split() == ["0%", "✗"]
    assert "red" in _style_of(cell, "✗")


def test_unobserved_activation_renders_as_a_dash():
    t = task([attempt(1, activated=None)], expected=None)
    assert _activation_cell(t, k=1).plain.strip() == "—"


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
                scored_tasks=1,
                per_task=[],
                avg_activation_score=0.733,
                activation_tasks=3,
                activation_per_skill=[
                    SkillActivationStats(
                        skill="mine", total=6, expected=2, fired=4, hits=2
                    )
                ],
            ),
        )
    )
    # Two columns, two footers: never one blended number.
    header = next(ln for ln in out.splitlines() if "Task" in ln)
    assert "success" in header and "activation" in header
    overall = next(ln for ln in out.splitlines() if "Overall" in ln)
    assert "100%" in overall and "73.3%" in overall
    # Per-skill diagnostic: fires when wanted 2/2, when not wanted 2/4.
    assert re.search(r"mine\s.*100%\s+2/2\s.*50%\s+2/4", out)


def test_a_timed_out_attempt_shows_what_it_activated_and_its_error():
    timed_out = attempt(1, outcome=Outcome.TIMEOUT, activated=["sleeper"])
    timed_out.assert_evidence = "timeout"
    tasks = [task([timed_out], ["sleeper"])]

    out = render(_results(tasks, AggregateScore(avg_score=0.0, per_task=[])))

    assert re.search(r"activated so far\s+sleeper", out)
    assert re.search(r"error\s+timeout", out)
    assert not re.search(r"assert\s+timeout", out)


def test_activation_line_is_absent_when_nothing_was_asserted():
    tasks = [task([attempt(1, activated=["mine"])], None)]
    out = render(_results(tasks, AggregateScore(avg_score=1.0, per_task=[])))
    assert "success" in out
    assert "activation" not in out.lower()


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
    assert "no execution checks" in out
    # No success column at all, so no 0% anywhere to misread.
    assert "success" not in out
    # The activation scoreboard is unaffected and still reports.
    assert "activation" in out


def _scored(name, outcomes):
    return TaskResult(
        task_id=name,
        task_name=name,
        attempts=[attempt(n, o) for n, o in enumerate(outcomes, start=1)],
    )


def test_overall_shows_the_pooled_count_when_it_agrees_with_the_average():
    tasks = [
        _scored("a", [Outcome.PASS, Outcome.PASS]),
        _scored("b", [Outcome.PASS, Outcome.TASK_FAIL]),
    ]
    out = render(_results(tasks, AggregateScore.from_task_results(tasks, k=2)))
    overall = next(ln for ln in out.splitlines() if "Overall" in ln)
    assert re.search(r"75%\s+3/4 ✓", overall)


def test_overall_names_what_it_averages_when_the_count_would_disagree():
    # 1/1 and 1/3 average to 66.7% but pool to 2/4 = 50%. Showing both would
    # make the reader pick one; the score is a mean of rates (docs/adr/0007).
    tasks = [
        _scored("a", [Outcome.PASS, Outcome.TIMEOUT, Outcome.TIMEOUT]),
        _scored("b", [Outcome.PASS, Outcome.TASK_FAIL, Outcome.TASK_FAIL]),
    ]
    out = render(_results(tasks, AggregateScore.from_task_results(tasks, k=3)))
    overall = next(ln for ln in out.splitlines() if "Overall" in ln)
    assert re.search(r"66.7%\s+avg of 2", overall)
    assert "2/4" not in overall


def test_a_finished_attempt_does_not_claim_it_stopped():
    finished = attempt(1, outcome=Outcome.TASK_FAIL, activated=["sleeper"])
    tasks = [task([finished], ["sleeper"])]

    out = render(_results(tasks, AggregateScore(avg_score=0.0, per_task=[])))

    assert "attempt 1" in out
    assert "activated so far" not in out
