"""The pre-registered bar: spec field, verdict, exit 3 (docs/adr/0035)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from conftest import ScriptedHarness, ScriptedJudge, agent_result, task_result
from pydantic import ValidationError
from typer.testing import CliRunner

from caliper.gate import Verdict, evaluate, wilson_interval
from caliper.main import app
from caliper.markdown import run_markdown
from caliper.runner import run
from caliper.runstore import RunStore
from caliper.schema.results import (
    AggregateScore,
    HookFailure,
    Outcome,
    RunMeta,
    RunResults,
)
from caliper.schema.spec import Bar, EvalSpec, TaskSpec, load_spec

P, F, T = Outcome.PASS, Outcome.TASK_FAIL, Outcome.TIMEOUT
runner = CliRunner()


def _results(bar: Bar | None, *tasks, **meta) -> RunResults:
    tasks = list(tasks)
    return RunResults(
        run=RunMeta(
            spec="sample",
            timestamp=datetime(2026, 10, 4, tzinfo=timezone.utc),
            k=max(len(t.attempts) for t in tasks),
            backend="claude-code",
            bar=bar,
            **meta,
        ),
        task_results=tasks,
        aggregate=AggregateScore.from_task_results(tasks, k=len(tasks[0].attempts)),
    )


# ── the interval ────────────────────────────────────────────────────────────


def test_wilson_interval_matches_the_textbook_value() -> None:
    low, high = wilson_interval(8, 10)
    assert low == pytest.approx(0.4902, abs=1e-4)
    assert high == pytest.approx(0.9433, abs=1e-4)


def test_wilson_interval_reaches_the_ends_exactly() -> None:
    assert wilson_interval(5, 5)[1] == 1.0
    assert wilson_interval(0, 5)[0] == 0.0


def test_no_trials_is_the_whole_range() -> None:
    assert wilson_interval(0, 0) == (0.0, 1.0)


# ── the verdict ─────────────────────────────────────────────────────────────


def test_no_bar_means_no_gate() -> None:
    assert evaluate(_results(None, task_result(F, F, F))) is None


def test_interval_wholly_above_the_bar_clears() -> None:
    gate = evaluate(_results(Bar(score=0.5), task_result(*[P] * 20)))
    assert gate.verdict is Verdict.CLEARED
    assert not gate.blocks


def test_interval_wholly_below_the_bar_misses_and_blocks() -> None:
    gate = evaluate(_results(Bar(score=0.9), task_result(P, F, F, F, F, F, F, F)))
    assert gate.verdict is Verdict.MISSED
    assert gate.blocks


def test_a_straddling_interval_is_inconclusive_and_passes_by_default() -> None:
    # 4/5 is a plausible draw from a skill that clears 0.8 and from one that
    # does not: blocking on it would block on noise.
    gate = evaluate(_results(Bar(score=0.8), task_result(P, P, P, P, F)))
    assert gate.verdict is Verdict.INCONCLUSIVE
    assert not gate.blocks


def test_on_inconclusive_fail_blocks_a_straddle() -> None:
    bar = Bar(score=0.8, on_inconclusive="fail")
    gate = evaluate(_results(bar, task_result(P, P, P, P, F)))
    assert gate.verdict is Verdict.INCONCLUSIVE
    assert gate.blocks


def test_rates_pool_attempts_across_tasks_and_skip_unusable_ones() -> None:
    gate = evaluate(
        _results(
            Bar(score=0.5),
            task_result(P, P, T, name="One"),
            task_result(P, F, T, task_id="task-002", name="Two"),
        )
    )
    (check,) = gate.checks
    # The timeouts left the denominator (docs/adr/0001).
    assert (check.successes, check.usable) == (3, 4)


def test_activation_bar_reads_the_activation_scoreboard() -> None:
    tr = task_result(P, P, P, expected=["grill"])
    for attempt in tr.attempts:
        attempt.activated, attempt.activation_passed = [], False
    gate = evaluate(_results(Bar(score=0.1, activation=0.9), tr))
    score, activation = gate.checks
    assert score.verdict is Verdict.CLEARED
    assert (activation.successes, activation.usable) == (0, 3)
    # One missed rate is a missed bar, whatever the other did.
    assert gate.verdict is Verdict.MISSED


@pytest.mark.parametrize(
    "meta",
    [
        {"ablated": ["grill"]},
        {"interrupted": True},
        {
            "hook_failures": [
                HookFailure(task_id="task-001", attempt=1, phase="setup", exit_code=1)
            ]
        },
    ],
)
def test_a_run_that_is_not_a_clean_full_measurement_is_not_gated(meta) -> None:
    gate = evaluate(_results(Bar(score=0.9), task_result(F, F, F, F, F), **meta))
    assert gate.verdict is Verdict.NOT_APPLIED
    assert not gate.blocks


# ── the spec ────────────────────────────────────────────────────────────────


def _spec(tmp_path, bar: str, task: str = "    assert: assert True\n"):
    path = tmp_path / "s.eval.yaml"
    path.write_text(f"{bar}tasks:\n  - name: One\n    prompt: Do it\n{task}")
    return load_spec(path)


def test_spec_declares_a_bar(tmp_path) -> None:
    spec = _spec(tmp_path, "bar:\n  score: 0.8\n  on_inconclusive: fail\n")
    assert spec.bar == Bar(score=0.8, on_inconclusive="fail")


@pytest.mark.parametrize(
    ("bar", "message"),
    [
        ("bar: {}\n", "at least one of"),
        ("bar:\n  score: 1.5\n", "less than or equal to 1"),
        ("bar:\n  scor: 0.8\n", "Extra inputs"),
        ("bar:\n  score: 0.8\n  on_inconclusive: maybe\n", "'pass' or 'fail'"),
        ("bar:\n  activation: 0.8\n", "needs a task with `activates:`"),
    ],
)
def test_spec_refuses_a_bar_that_cannot_gate(tmp_path, bar, message) -> None:
    with pytest.raises(ValidationError, match=message):
        _spec(tmp_path, bar)


def test_score_bar_on_a_probe_only_spec_is_refused(tmp_path) -> None:
    with pytest.raises(ValidationError, match="trigger probe"):
        _spec(tmp_path, "bar:\n  score: 0.8\n", task="    activates: []\n")


def test_runner_records_the_bar_the_run_was_held_to(tmp_path) -> None:
    spec_path = tmp_path / "s.eval.yaml"
    spec_path.write_text("tasks: []\n")
    spec = EvalSpec(
        bar=Bar(score=0.7),
        tasks=[TaskSpec(id="task-001", name="One", prompt="Do it", expect="ok")],
    )
    results = run(
        spec=spec,
        spec_path=spec_path,
        harness=ScriptedHarness(agent_result()),
        judge=ScriptedJudge(),
        k=1,
        workers=1,
        timeout=30,
    )
    assert results.run.bar == Bar(score=0.7)


# ── the exit code ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("outcomes", "bar", "code"),
    [
        ([P] * 10, "score: 0.5", 0),
        ([F] * 10, "score: 0.5", 3),
        ([P, P, P, P, F], "score: 0.8", 0),
        ([P, P, P, P, F], "score: 0.8\n  on_inconclusive: fail", 3),
    ],
)
def test_run_exits_three_only_when_the_bar_blocks(
    monkeypatch, tmp_path, outcomes, bar, code
) -> None:
    from test_run_cli import _stub_a_run

    monkeypatch.chdir(tmp_path)
    spec_file = tmp_path / "sample.eval.yaml"
    spec_file.write_text(
        f"bar:\n  {bar}\ntasks:\n  - name: One\n    prompt: Do it\n"
        "    assert: assert True\n"
    )
    finished = _results(load_spec(spec_file).bar, task_result(*outcomes))
    _stub_a_run(monkeypatch, finished)

    result = runner.invoke(app, ["run", str(spec_file)])

    assert result.exit_code == code, result.output


def test_could_not_run_wins_over_a_missed_bar(monkeypatch, tmp_path) -> None:
    from test_run_cli import _stub_a_run

    monkeypatch.chdir(tmp_path)
    spec_file = tmp_path / "sample.eval.yaml"
    spec_file.write_text(
        "bar:\n  score: 0.9\ntasks:\n  - name: One\n    prompt: Do it\n"
        "    assert: assert True\n"
    )
    failure = HookFailure(task_id="task-001", attempt=1, phase="cleanup", exit_code=9)
    finished = _results(Bar(score=0.9), task_result(F, F, F), hook_failures=[failure])
    _stub_a_run(monkeypatch, finished)

    result = runner.invoke(app, ["run", str(spec_file)])

    # A broken pipeline is never reported as a failing skill.
    assert result.exit_code == 2, result.output


# ── what CI reads ───────────────────────────────────────────────────────────


def test_markdown_leads_with_the_verdict() -> None:
    text = run_markdown(_results(Bar(score=0.9), task_result(F, F, F, F, F, F)))
    assert "**Bar:** ❌ missed" in text
    assert "0/6" in text


def test_report_formats_carry_the_gate(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    store = RunStore.discover()
    path = store.save(_results(Bar(score=0.9), task_result(F, F, F, F, F, F)))

    as_json = runner.invoke(app, ["report", str(path), "--format", "json"])
    as_md = runner.invoke(app, ["report", str(path), "--format", "markdown"])
    as_table = runner.invoke(app, ["report", str(path)])

    assert as_json.exit_code == 0, as_json.output
    assert '"verdict": "missed"' in as_json.output
    assert "❌ missed" in as_md.output
    assert "missed" in as_table.output
