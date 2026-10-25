from __future__ import annotations

import io
from datetime import datetime, timezone

from rich.console import Console

from caliper.reporter import (
    _OUTPUT_TRUNCATE_AT,
    _format_output,
    make_progress,
    print_results,
    update_progress,
)
from caliper.schema.results import (
    AggregateScore,
    AttemptRecord,
    HookFailure,
    McpUsage,
    Outcome,
    OutcomeCounts,
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


def test_progress_shows_a_task_name_literally() -> None:
    """A valid task name is text, never Rich markup: ``[/]`` used to crash a run."""
    names = ["[/]", "[bold]loud[/bold]"]
    progress, _ = make_progress(names, k=1)
    buf = io.StringIO()

    Console(file=buf, width=120).print(progress.make_tasks_table(progress.tasks))

    assert "[/]" in buf.getvalue()
    assert "[bold]loud[/bold]" in buf.getvalue()


def test_update_progress_marks_early_stopped_task_finished() -> None:
    progress, task_ids = make_progress(["Task one"], k=3)

    update_progress(
        progress,
        task_ids,
        "Task one",
        k=3,
        counts=OutcomeCounts([Outcome.INFRA_ERROR]),
        finished=True,
    )

    task = progress.tasks[task_ids["Task one"]]
    assert task.completed == 3
    # The attempts that never ran stay visible as such, not as failures.
    assert task.fields["marks"].plain == "⊘ · ·"


def test_cheat_remains_visible_when_cleanup_fails() -> None:
    task = TaskResult(
        task_id="task-001",
        task_name="Cheat",
        attempts=[
            AttemptRecord(
                attempt=1,
                output="",
                duration_seconds=0.1,
                outcome=Outcome.CHEAT,
                hook_failures=[
                    HookFailure(
                        task_id="task-001",
                        attempt=1,
                        phase="cleanup",
                        exit_code=9,
                    )
                ],
            )
        ],
    )

    out = _render(_make_results([task]))

    # The cleanup failure is reported, and the cheat is still named beside it.
    assert "cleanup hook" in out
    assert "1 cheat" in out
    assert "⚠" in next(ln for ln in out.splitlines() if "Cheat" in ln)


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
        outcome=Outcome.PASS if passed else Outcome.TASK_FAIL,
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
    )


def _make_results(task_results: list[TaskResult]) -> RunResults:
    return RunResults(
        run=RunMeta(
            spec="test-spec",
            timestamp=datetime(2026, 6, 21, 12, 0, 0, tzinfo=timezone.utc),
            k=1,
            backend="claude-code",
        ),
        skill_snapshots=[SkillSnapshot(path="/fake/SKILL.md")],
        task_results=task_results,
        aggregate=AggregateScore.from_task_results(task_results, k=1),
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
    assert result == "hello world"


def test_format_output_long_string_truncated() -> None:
    tail = "tail:" + "x" * (_OUTPUT_TRUNCATE_AT - 5)
    long_output = "discarded-prefix:" + "a" * 100 + tail
    result = _format_output(long_output)
    assert "truncated" in result
    assert result.split("\n", 1)[1] == tail
    assert "discarded-prefix" not in result


def test_format_output_exact_limit_not_truncated() -> None:
    exact = "y" * _OUTPUT_TRUNCATE_AT
    result = _format_output(exact)
    assert result == exact


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


def test_judge_input_shows_only_under_verbose() -> None:
    """The judge's expectation and script are for inspecting, not the default."""
    task = _make_task("task-001", passed=False, autorater_reasoning="no file")
    task.expect = "writes the EXPECTED file"
    task.attempts[0].autorater_script = "assert open('SCRIPTED').read()"
    results = _make_results([task])

    default = _render(results)
    verbose = _render(results, verbose=True)

    assert "no file" in default
    assert "EXPECTED" not in default
    assert "SCRIPTED" not in default
    assert "writes the EXPECTED file" in verbose
    assert "assert open('SCRIPTED').read()" in verbose
    saved = RunResults.model_validate_json(results.model_dump_json())
    assert saved.task_results[0].expect == "writes the EXPECTED file"
    assert saved.task_results[0].attempts[0].autorater_script == (
        "assert open('SCRIPTED').read()"
    )


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
            _make_task(
                "task-002",
                passed=False,
                output="fail output",
                assert_evidence="file not found",
            ),
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
    tail = "z" * (_OUTPUT_TRUNCATE_AT - 4) + "TAIL"
    long_output = "DISCARDED" + "a" * 200 + tail
    results = _make_results([_make_task("task-001", passed=False, output=long_output)])
    out = _render(results)
    assert "truncated" in out
    assert "DISCARDED" not in out
    assert "TAIL" in out
    assert out.split("truncated", 1)[1].count("z") == _OUTPUT_TRUNCATE_AT - 4


def test_empty_output_renders_no_output_marker() -> None:
    results = _make_results([_make_task("task-001", passed=False, output="")])
    out = _render(results)
    assert "no output" in out


def test_aborted_unusable_task_is_reported_as_aborted() -> None:
    task = TaskResult(
        task_id="task-001",
        task_name="Task task-001",
        attempts=[
            AttemptRecord(
                attempt=1,
                output="",
                duration_seconds=1.0,
                outcome=Outcome.INFRA_ERROR,
                assert_evidence="spending cap",
            )
        ],
    )
    results = RunResults(
        run=RunMeta(
            spec="test-spec",
            timestamp=datetime(2026, 6, 21, 12, 0, 0, tzinfo=timezone.utc),
            k=3,
            backend="claude-code",
        ),
        skill_snapshots=[SkillSnapshot(path="/fake/SKILL.md")],
        task_results=[task],
        aggregate=AggregateScore(
            avg_score=0.0,
            per_task=[
                TaskScore(
                    task_id=task.task_id,
                    task_name=task.task_name,
                    successes=task.successes,
                    k=3,
                    score=None,
                )
            ],
        ),
    )

    out = _render(results)

    assert "aborted" in out
    assert "after 1 of 3 attempts" in out


def test_early_stopped_task_with_usable_pass_is_not_reported_as_aborted() -> None:
    task = TaskResult(
        task_id="task-001",
        task_name="Task task-001",
        attempts=[
            AttemptRecord(
                attempt=1,
                output="ok",
                duration_seconds=1.0,
                outcome=Outcome.PASS,
            ),
            AttemptRecord(
                attempt=2,
                output="",
                duration_seconds=1.0,
                outcome=Outcome.INFRA_ERROR,
                assert_evidence="spending cap",
            ),
            AttemptRecord(
                attempt=3,
                output="",
                duration_seconds=1.0,
                outcome=Outcome.INFRA_ERROR,
                assert_evidence="spending cap",
            ),
        ],
    )
    results = RunResults(
        run=RunMeta(
            spec="test-spec",
            timestamp=datetime(2026, 6, 21, 12, 0, 0, tzinfo=timezone.utc),
            k=5,
            backend="claude-code",
        ),
        skill_snapshots=[SkillSnapshot(path="/fake/SKILL.md")],
        task_results=[task],
        aggregate=AggregateScore(
            avg_score=1.0,
            per_task=[
                TaskScore(
                    task_id=task.task_id,
                    task_name=task.task_name,
                    successes=task.successes,
                    k=5,
                    score=1.0,
                )
            ],
        ),
    )

    out = _render(results, verbose=True)

    assert "aborted" not in out
    assert "100%" in next(ln for ln in out.splitlines() if "Task task-001" in ln)


# ---------------------------------------------------------------------------
# autorater_reasoning shown for failed tasks
# ---------------------------------------------------------------------------


def test_autorater_reasoning_shown_for_failed_task() -> None:
    results = _make_results(
        [_make_task("task-001", passed=False, autorater_reasoning="judge said no")]
    )
    out = _render(results)
    assert "judge said no" in out


# ---------------------------------------------------------------------------
# Live progress counts and failure-panel rendering
# ---------------------------------------------------------------------------


def test_update_progress_fills_the_marks_the_report_will_show() -> None:
    progress, task_ids = make_progress(["Task one"], k=5)

    counts = OutcomeCounts([Outcome.PASS, Outcome.TASK_FAIL, Outcome.PASS])
    update_progress(progress, task_ids, "Task one", k=5, counts=counts)

    marks = progress.tasks[task_ids["Task one"]].fields["marks"]
    assert marks.plain == "✓ ✗ ✓ · ·"


def test_update_progress_tallies_above_five_attempts() -> None:
    progress, task_ids = make_progress(["Task one"], k=10)

    counts = OutcomeCounts([Outcome.PASS] * 6 + [Outcome.TASK_FAIL])
    update_progress(progress, task_ids, "Task one", k=10, counts=counts)

    marks = progress.tasks[task_ids["Task one"]].fields["marks"]
    assert marks.plain == "✓6 ✗1 ·3"


def test_update_progress_keeps_a_finished_trigger_probe_neutral() -> None:
    progress, task_ids = make_progress(["Probe"], k=3)

    update_progress(
        progress,
        task_ids,
        "Probe",
        k=3,
        counts=OutcomeCounts([Outcome.NOT_CHECKED] * 3),
    )

    # Not a red ✗: a trigger probe asked no execution question.
    marks = progress.tasks[task_ids["Probe"]].fields["marks"]
    assert marks.plain == "— — —"
    assert all("red" not in str(span.style) for span in marks.spans)


def _render_markup(results: RunResults) -> str:
    buf = io.StringIO()
    con = Console(file=buf, highlight=False, width=120)
    import caliper.reporter as reporter_mod

    orig = reporter_mod.console
    reporter_mod.console = con
    try:
        print_results(results)
    finally:
        reporter_mod.console = orig
    return buf.getvalue()


def test_agent_output_that_looks_like_markup_renders_verbatim() -> None:
    results = _make_results(
        [_make_task("task-001", passed=False, output="see [bold]x[/] and [/dim]")]
    )

    assert "see [bold]x[/] and [/dim]" in _render_markup(results)


def test_trigger_probe_attempt_shows_its_activation_verdict() -> None:
    attempts = [
        AttemptRecord(
            attempt=n,
            output="",
            duration_seconds=1.0,
            outcome=Outcome.NOT_CHECKED,
            activated=activated,
            activation_passed=activated == ["wanted"],
        )
        for n, activated in ((1, ["other"]), (2, ["wanted"]))
    ]
    task = TaskResult(
        task_id="probe",
        task_name="Probe",
        attempts=attempts,
        activation_expected=["wanted"],
    )

    results = RunResults(
        run=RunMeta(
            spec="test-spec",
            timestamp=datetime(2026, 6, 21, 12, 0, 0, tzinfo=timezone.utc),
            k=2,
            backend="claude-code",
        ),
        skill_snapshots=[SkillSnapshot(path="/fake/SKILL.md")],
        task_results=[task],
        aggregate=AggregateScore.from_task_results([task], k=2),
    )

    out = _render_markup(results)

    assert "✗ attempt 1" in out
    assert "✓ attempt 2" in out


def test_built_in_activations_are_shown_without_being_scored() -> None:
    attempts = [
        AttemptRecord(
            attempt=n,
            output="",
            duration_seconds=1.0,
            outcome=Outcome.NOT_CHECKED,
            activated=[],
            activation_passed=True,
            builtin_activated=builtin,
        )
        for n, builtin in ((1, ["claude-api"]), (2, []))
    ]
    task = TaskResult(
        task_id="probe",
        task_name="Probe",
        attempts=attempts,
        activation_expected=[],
    )
    results = RunResults(
        run=RunMeta(
            spec="test-spec",
            timestamp=datetime(2026, 6, 21, 12, 0, 0, tzinfo=timezone.utc),
            k=2,
            backend="claude-code",
        ),
        task_results=[task],
        aggregate=AggregateScore.from_task_results([task], k=2),
    )

    out = _render_markup(results)

    assert "100%  2/2 ✓" in next(ln for ln in out.splitlines() if "Overall" in ln)
    assert (
        "built-in skills  claude-api 1/2  ship with claude-code · shown, not scored"
        in out
    )


def test_truncating_escaped_markup_cannot_expose_a_tag() -> None:
    # 501 chars: the cut lands right after "x", where escaping would have put
    # the backslash that shields "[/dim]".
    output = "x[/dim]" + "y" * (_OUTPUT_TRUNCATE_AT - 6)
    results = _make_results([_make_task("task-001", passed=False, output=output)])

    out = _render_markup(results)

    assert "[/dim]" + "y" * 10 in out


def test_empty_output_marker_is_visible_when_markup_is_rendered() -> None:
    results = _make_results([_make_task("task-001", passed=False, output="")])

    assert "[no output]" in _render_markup(results)


def test_each_outcome_is_counted_once():
    counts = OutcomeCounts()
    for outcome in (
        Outcome.PASS,
        Outcome.TASK_FAIL,
        Outcome.CHEAT,
        Outcome.TIMEOUT,
        Outcome.NOT_CHECKED,
    ):
        counts.add(outcome)

    assert (counts.completed, counts.successes, counts.failed) == (5, 1, 2)
    assert (counts.unusable, counts.unchecked, counts.usable) == (1, 1, 3)


def test_a_cheat_stays_counted_after_clean_attempts():
    counts = OutcomeCounts([Outcome.CHEAT, Outcome.PASS, Outcome.PASS])

    assert counts.cheated


def test_a_finished_task_counts_like_its_attempts_did_live():
    outcomes = [Outcome.PASS, Outcome.JUDGE_ERROR, Outcome.NOT_CHECKED]
    result = TaskResult(
        task_id="task-001",
        task_name="t",
        attempts=[
            AttemptRecord(attempt=n, output="", duration_seconds=0.1, outcome=o)
            for n, o in enumerate(outcomes, 1)
        ],
    )

    assert (result.counts.completed, result.counts.successes, result.counts.failed) == (
        3,
        1,
        0,
    )
    assert (result.counts.usable, result.counts.unusable, result.counts.unchecked) == (
        1,
        1,
        1,
    )


# ---------------------------------------------------------------------------
# Review fixes: live order, usable-only costs, MCP attribution, hook output
# ---------------------------------------------------------------------------


def test_live_marks_sit_in_their_attempt_slot_whatever_the_finish_order() -> None:
    progress, task_ids = make_progress(["Task one"], k=3)

    # Attempt 2 finished first; it must not take attempt 1's slot.
    update_progress(
        progress,
        task_ids,
        "Task one",
        k=3,
        counts=OutcomeCounts([Outcome.TASK_FAIL]),
        by_attempt={2: Outcome.TASK_FAIL},
    )

    task = progress.tasks[task_ids["Task one"]]
    assert task.fields["marks"].plain == "· ✗ ·"
    # The count comes from the same snapshot as the marks.
    assert task.completed == 1


def _mcp_run(transcript, servers, backend="claude-code") -> RunResults:
    from caliper.schema.results import TranscriptTurn

    task = TaskResult(
        task_id="task-001",
        task_name="t",
        attempts=[
            AttemptRecord(
                attempt=1,
                output="",
                duration_seconds=1.0,
                outcome=Outcome.PASS,
                transcript=[
                    TranscriptTurn(role=role, content="", tool_name=name)
                    for role, name in transcript
                ],
            )
        ],
    )
    results = _make_results([task])
    results.run.mcp_servers = servers
    results.run.backend = backend
    return results


def _mcp_calls(results):
    """Per server: (attempts that called it, attempts observed, total calls)."""
    return {
        row.server: (row.used, row.observed, row.calls)
        for row in McpUsage.from_run_results(results)
    }


def test_a_hermes_tool_result_is_not_counted_as_a_second_call() -> None:
    results = _mcp_run(
        [("tool_use", "mcp_mail_read"), ("tool_result", "mcp_mail_read")],
        ["mail"],
        backend="hermes",
    )
    assert _mcp_calls(results)["mail"] == (1, 1, 1)


def test_a_hermes_call_is_credited_to_the_longest_matching_server() -> None:
    results = _mcp_run(
        [("tool_use", "mcp_mail_archive_read")],
        ["mail", "mail_archive"],
        backend="hermes",
    )
    stats = _mcp_calls(results)
    assert stats["mail_archive"] == (1, 1, 1)
    assert stats["mail"] == (0, 1, 0)


def test_a_hook_failure_without_an_attempt_record_shows_its_output() -> None:
    # A cancelled attempt leaves no record, so no panel can carry the output.
    results = _make_results([_make_task("task-001", passed=True)])
    results.run.hook_failures = [
        HookFailure(
            task_id="task-001",
            attempt=2,
            phase="cleanup",
            exit_code=9,
            output="database teardown failed",
        )
    ]
    out = _render(results)

    assert "cleanup hook exited 9" in out
    assert "database teardown failed" in out
    assert "output in the task's panel" not in out


def test_a_cost_from_a_zero_baseline_is_shown_absolute() -> None:
    from caliper.reporter import _fmt_tokens, _relative

    assert _relative(0, 500, lambda n: _fmt_tokens(int(n))).plain == "+500"
    assert _relative(0, 0, _fmt_tokens).plain == "—"


def test_a_server_name_containing_the_separator_is_matched_whole() -> None:
    # Server names may contain "__": split on the first one and this call
    # would land on `mail`.
    results = _mcp_run(
        [("tool_use", "mcp__mail__archive__read")], ["mail", "mail__archive"]
    )
    stats = _mcp_calls(results)
    assert stats["mail__archive"] == (1, 1, 1)
    assert stats["mail"] == (0, 1, 0)


def test_a_hermes_server_with_a_leading_underscore_is_counted() -> None:
    # `mcp__mail_read` is hermes' single-underscore form for server `_mail`;
    # the backend, not the name, says which form it is.
    results = _mcp_run([("tool_use", "mcp__mail_read")], ["_mail"], backend="hermes")
    assert _mcp_calls(results)["_mail"] == (1, 1, 1)


def test_the_header_names_the_cli_build_that_ran() -> None:
    results = _make_results([_make_task("task-001", passed=True)])
    results.run.cli_version = "2.1.281 (Claude Code)"

    cli_row = next(ln for ln in _render(results).splitlines() if "cli" in ln.split())
    assert cli_row.split() == ["cli", "2.1.281", "(Claude", "Code)"]


def test_a_run_saved_before_cli_versions_has_no_cli_row() -> None:
    out = _render(_make_results([_make_task("task-001", passed=True)]))

    assert not any("cli" in ln.split() for ln in out.splitlines())
