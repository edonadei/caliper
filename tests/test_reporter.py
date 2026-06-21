from __future__ import annotations

import io
from datetime import datetime, timezone

from rich.console import Console

from caliper.reporter import _OUTPUT_TRUNCATE_AT, _format_output, make_progress, print_results
from caliper.schema.results import (
    AggregateScore,
    AttemptRecord,
    RunMeta,
    RunResults,
    SkillSnapshot,
    TaskResult,
    TaskScore,
)


def test_make_progress_initializes_task_totals() -> None:
    progress, task_ids = make_progress(["Task one", "Task two"], k=3)

    assert progress.tasks[task_ids["Task one"]].total == 3
    assert progress.tasks[task_ids["Task two"]].total == 3


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_attempt(
    *,
    passed: bool,
    output: str = "some output",
    assert_evidence: str | None = None,
    autorater_reasoning: str | None = None,
) -> AttemptRecord:
    return AttemptRecord(
        attempt=1,
        output=output,
        duration_seconds=5.0,
        passed=passed,
        assert_passed=passed if assert_evidence is None else not passed,
        assert_evidence=assert_evidence,
        autorater_passed=None,
        autorater_reasoning=autorater_reasoning,
    )


def _make_task(
    task_id: str,
    *,
    passed: bool,
    output: str = "some output",
    assert_evidence: str | None = None,
    autorater_reasoning: str | None = None,
) -> TaskResult:
    attempt = _make_attempt(
        passed=passed,
        output=output,
        assert_evidence=assert_evidence,
        autorater_reasoning=autorater_reasoning,
    )
    return TaskResult(
        task_id=task_id,
        task_name=f"Task {task_id}",
        attempts=[attempt],
        successes=1 if passed else 0,
        pass_at_k=1.0 if passed else 0.0,
    )


def _make_results(task_results: list[TaskResult]) -> RunResults:
    scores = [
        TaskScore(
            task_id=tr.task_id,
            task_name=tr.task_name,
            k=1,
            successes=tr.successes,
            score=tr.pass_at_k,
        )
        for tr in task_results
    ]
    return RunResults(
        run=RunMeta(
            spec="test-spec",
            timestamp=datetime(2026, 6, 21, 12, 0, 0, tzinfo=timezone.utc),
            k=1,
            backend="claude-code",
        ),
        skill_snapshot=SkillSnapshot(path="/fake/SKILL.md"),
        task_results=task_results,
        aggregate=AggregateScore(
            avg_pass_at_k=sum(tr.pass_at_k for tr in task_results) / len(task_results),
            per_task=scores,
        ),
    )


def _render(results: RunResults, *, verbose: bool = False) -> str:
    buf = io.StringIO()
    con = Console(file=buf, highlight=False, markup=False, width=120)
    # Temporarily swap the module-level console
    import caliper.reporter as reporter_mod

    orig = reporter_mod.console
    reporter_mod.console = con
    try:
        print_results(results, verbose=verbose)
    finally:
        reporter_mod.console = orig
    return buf.getvalue()


# ---------------------------------------------------------------------------
# _format_output unit tests
# ---------------------------------------------------------------------------


def test_format_output_empty_string() -> None:
    assert "[no output]" in _format_output("")


def test_format_output_whitespace_only() -> None:
    assert "[no output]" in _format_output("   \n  ")


def test_format_output_short_string_unchanged() -> None:
    result = _format_output("hello world")
    assert "hello world" in result
    assert "truncated" not in result


def test_format_output_long_string_truncated() -> None:
    long_output = "x" * (_OUTPUT_TRUNCATE_AT + 100)
    result = _format_output(long_output)
    assert "truncated" in result
    # The tail (last 500 chars) should be present
    assert "x" * _OUTPUT_TRUNCATE_AT in result


def test_format_output_exact_limit_not_truncated() -> None:
    exact = "y" * _OUTPUT_TRUNCATE_AT
    result = _format_output(exact)
    assert "truncated" not in result
    assert exact in result


# ---------------------------------------------------------------------------
# print_results default mode (failed tasks shown, passing tasks not)
# ---------------------------------------------------------------------------


def test_failed_task_assert_evidence_shown_by_default() -> None:
    results = _make_results(
        [_make_task("task-001", passed=False, assert_evidence="timeout")]
    )
    out = _render(results)
    assert "timeout" in out


def test_failed_task_output_shown_by_default() -> None:
    results = _make_results(
        [_make_task("task-001", passed=False, output="the agent said this")]
    )
    out = _render(results)
    assert "the agent said this" in out


def test_passing_task_detail_not_shown_by_default() -> None:
    results = _make_results(
        [_make_task("task-001", passed=True, output="passing output")]
    )
    out = _render(results)
    assert "passing output" not in out


def test_only_failed_tasks_shown_in_mixed_results() -> None:
    results = _make_results(
        [
            _make_task("task-001", passed=True, output="pass output"),
            _make_task("task-002", passed=False, output="fail output", assert_evidence="file not found"),
        ]
    )
    out = _render(results)
    assert "fail output" in out
    assert "file not found" in out
    assert "pass output" not in out


# ---------------------------------------------------------------------------
# print_results verbose mode (all tasks shown)
# ---------------------------------------------------------------------------


def test_verbose_shows_passing_task_detail() -> None:
    results = _make_results(
        [_make_task("task-001", passed=True, output="passing output")]
    )
    out = _render(results, verbose=True)
    assert "passing output" in out


def test_verbose_shows_all_tasks() -> None:
    results = _make_results(
        [
            _make_task("task-001", passed=True, output="pass output"),
            _make_task("task-002", passed=False, output="fail output"),
        ]
    )
    out = _render(results, verbose=True)
    assert "pass output" in out
    assert "fail output" in out


# ---------------------------------------------------------------------------
# Truncation in rendered output
# ---------------------------------------------------------------------------


def test_long_output_truncated_in_rendered_output() -> None:
    long_output = "z" * (_OUTPUT_TRUNCATE_AT + 200)
    results = _make_results(
        [_make_task("task-001", passed=False, output=long_output)]
    )
    out = _render(results)
    assert "truncated" in out
    # Rich wraps long lines; count total z's to verify the tail was included
    assert out.count("z") >= _OUTPUT_TRUNCATE_AT


def test_empty_output_renders_no_output_marker() -> None:
    results = _make_results(
        [_make_task("task-001", passed=False, output="")]
    )
    out = _render(results)
    assert "no output" in out


# ---------------------------------------------------------------------------
# autorater_reasoning shown for failed tasks
# ---------------------------------------------------------------------------


def test_autorater_reasoning_shown_for_failed_task() -> None:
    results = _make_results(
        [_make_task("task-001", passed=False, autorater_reasoning="judge said no")]
    )
    out = _render(results)
    assert "judge said no" in out
