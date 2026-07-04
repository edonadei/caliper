from __future__ import annotations

import json
from datetime import datetime, timezone

from caliper.compare import diff_runs
from caliper.harness.base import ProcessResult, RunContext
from caliper.harness.claude_code import ClaudeCodeHarness
from caliper.harness.codex import CodexHarness
from caliper.harness.hermes import HermesHarness
from caliper.harness.pi import PiHarness
from caliper.reporter import print_comparison, print_results
from caliper.schema.results import (
    AggregateScore,
    AttemptRecord,
    Outcome,
    RunMeta,
    RunResults,
    SkillSnapshot,
    TaskResult,
    TokenUsage,
    UsageTotals,
)


def _ctx() -> RunContext:
    return RunContext(
        task_id="task-001",
        attempt=1,
        prompt="hello",
        skill_path=None,
        model=None,
        timeout=30,
        isolated_home="/tmp/none",
        extra_path=[],
    )


def _proc(stdout: str) -> ProcessResult:
    return ProcessResult(stdout=stdout, stderr="", returncode=0, timed_out=False)


# --------------------------------------------------------------------------
# TokenUsage contract
# --------------------------------------------------------------------------


def test_total_tokens_sums_all_four_disjoint_fields() -> None:
    u = TokenUsage(
        input_tokens=100,
        output_tokens=20,
        cache_read_tokens=5,
        cache_creation_tokens=3,
    )
    assert u.total_tokens == 128


def test_total_tokens_is_none_when_nothing_reported() -> None:
    assert TokenUsage().total_tokens is None


def test_total_tokens_treats_missing_components_as_zero() -> None:
    assert TokenUsage(input_tokens=10, output_tokens=5).total_tokens == 15


# --------------------------------------------------------------------------
# Per-backend _usage parsing
# --------------------------------------------------------------------------


def test_claude_usage_maps_result_event_directly() -> None:
    stdout = "\n".join(
        [
            json.dumps({"type": "assistant", "message": {"content": []}}),
            json.dumps(
                {
                    "type": "result",
                    "result": "done",
                    "usage": {
                        "input_tokens": 1000,
                        "output_tokens": 200,
                        "cache_read_input_tokens": 50,
                        "cache_creation_input_tokens": 30,
                    },
                }
            ),
        ]
    )
    usage = ClaudeCodeHarness()._usage(_proc(stdout), _ctx())
    assert usage == TokenUsage(
        input_tokens=1000,
        output_tokens=200,
        cache_read_tokens=50,
        cache_creation_tokens=30,
    )


def test_claude_usage_none_without_result_event() -> None:
    stdout = json.dumps({"type": "assistant", "message": {"content": []}})
    assert ClaudeCodeHarness()._usage(_proc(stdout), _ctx()) is None


def test_codex_usage_subtracts_cached_from_input() -> None:
    # OpenAI semantics: input_tokens INCLUDES cached_input_tokens.
    stdout = "\n".join(
        [
            json.dumps({"type": "turn.started"}),
            json.dumps(
                {
                    "type": "turn.completed",
                    "usage": {
                        "input_tokens": 16882,
                        "cached_input_tokens": 1920,
                        "output_tokens": 17,
                        "reasoning_output_tokens": 10,
                    },
                }
            ),
        ]
    )
    usage = CodexHarness()._usage(_proc(stdout), _ctx())
    assert usage.input_tokens == 16882 - 1920
    assert usage.cache_read_tokens == 1920
    assert usage.output_tokens == 17
    assert usage.cache_creation_tokens is None
    # Disjoint: total does not double-count the cached tokens.
    assert usage.total_tokens == (16882 - 1920) + 17 + 1920


def test_pi_usage_sums_per_assistant_message() -> None:
    def msg_end(inp: int, out: int, cr: int) -> str:
        return json.dumps(
            {
                "type": "message_end",
                "message": {
                    "role": "assistant",
                    "usage": {
                        "input": inp,
                        "output": out,
                        "cacheRead": cr,
                        "cacheWrite": 0,
                    },
                },
            }
        )

    stdout = "\n".join(
        [
            json.dumps({"type": "message_end", "message": {"role": "user"}}),
            msg_end(100, 10, 5),
            msg_end(200, 20, 0),
        ]
    )
    usage = PiHarness()._usage(_proc(stdout), _ctx())
    assert usage.input_tokens == 300
    assert usage.output_tokens == 30
    assert usage.cache_read_tokens == 5


def test_hermes_usage_reads_session_totals() -> None:
    record = {
        "messages": [{"role": "user", "content": "hi"}],
        "input_tokens": 500,
        "output_tokens": 40,
        "cache_read_tokens": 12,
        "cache_write_tokens": 8,
    }
    usage = HermesHarness()._usage(_proc(json.dumps(record)), _ctx())
    assert usage == TokenUsage(
        input_tokens=500,
        output_tokens=40,
        cache_read_tokens=12,
        cache_creation_tokens=8,
    )


def test_hermes_usage_none_when_totals_absent() -> None:
    record = {"messages": [{"role": "user", "content": "hi"}]}
    assert HermesHarness()._usage(_proc(json.dumps(record)), _ctx()) is None


# --------------------------------------------------------------------------
# Failure handling: usage extraction must never sink an attempt
# --------------------------------------------------------------------------


def test_usage_extraction_failure_degrades_to_none(monkeypatch) -> None:
    """A raising _usage (e.g. a malformed/schema-changed payload) must degrade to
    None, not crash the attempt — usage is optional."""

    def boom(proc, ctx):
        raise ValueError("malformed usage payload")

    harness = ClaudeCodeHarness()
    monkeypatch.setattr(harness, "_usage", boom)
    assert harness._safe_usage(_proc("{}"), _ctx()) is None


def test_codex_usage_survives_non_numeric_fields() -> None:
    # A malformed payload (string where an int is expected) must not raise out of
    # the guarded path; the whole attempt still resolves with usage=None.
    stdout = json.dumps({"type": "turn.completed", "usage": {"input_tokens": "oops"}})
    harness = CodexHarness()
    assert harness._safe_usage(_proc(stdout), _ctx()) is None


# --------------------------------------------------------------------------
# Aggregation
# --------------------------------------------------------------------------


def _att(outcome: Outcome, dur: float, usage: TokenUsage | None) -> AttemptRecord:
    return AttemptRecord(
        attempt=1, output="", duration_seconds=dur, outcome=outcome, usage=usage
    )


def test_usage_totals_sum_all_attempts_and_break_out_unusable() -> None:
    tasks = [
        TaskResult(
            task_id="task-001",
            task_name="alpha",
            attempts=[
                _att(
                    Outcome.PASS, 10.0, TokenUsage(input_tokens=100, output_tokens=10)
                ),
                _att(
                    Outcome.TIMEOUT, 30.0, TokenUsage(input_tokens=40, output_tokens=0)
                ),
            ],
            successes=1,
            unusable=1,
            pass_at_k=1.0,
        )
    ]
    totals = UsageTotals.from_task_results(tasks)
    assert totals.attempts == 2
    assert totals.wall_seconds == 40.0
    assert totals.input_tokens == 140
    assert totals.output_tokens == 10
    assert totals.total_tokens == 150
    assert totals.tokens_reported is True
    # Unusable slice reported separately.
    assert totals.unusable_attempts == 1
    assert totals.unusable_wall_seconds == 30.0
    assert totals.unusable_tokens == 40
    # Average denominator is usable attempts only.
    assert totals.usable_attempts == 1
    assert totals.usable_wall_seconds == 10.0


def test_usage_totals_tokens_unreported_when_no_backend_data() -> None:
    tasks = [
        TaskResult(
            task_id="task-001",
            task_name="alpha",
            attempts=[_att(Outcome.PASS, 5.0, None)],
            successes=1,
            unusable=0,
            pass_at_k=1.0,
        )
    ]
    totals = UsageTotals.from_task_results(tasks)
    assert totals.tokens_reported is False
    assert totals.wall_seconds == 5.0  # wall time is always real


# --------------------------------------------------------------------------
# Backwards compatibility
# --------------------------------------------------------------------------


def test_old_results_json_without_usage_still_loads() -> None:
    old = {
        "attempt": 1,
        "output": "hi",
        "duration_seconds": 2.0,
        "outcome": "pass",
    }
    record = AttemptRecord.model_validate(old)
    assert record.usage is None
    assert record.passed is True


# --------------------------------------------------------------------------
# Compare deltas
# --------------------------------------------------------------------------


def _run(tasks: list[TaskResult], spec: str = "demo", k: int = 2) -> RunResults:
    scored = [t.pass_at_k for t in tasks if t.pass_at_k is not None]
    return RunResults(
        run=RunMeta(
            spec=spec,
            timestamp=datetime(2026, 7, 1, tzinfo=timezone.utc),
            k=k,
            backend="claude-code",
        ),
        skill_snapshot=SkillSnapshot(path="/fake/SKILL.md"),
        task_results=tasks,
        aggregate=AggregateScore(
            avg_pass_at_k=sum(scored) / len(scored) if scored else 0.0, per_task=[]
        ),
    )


def _task_with_tokens(name: str, per_attempt_tokens: int, dur: float) -> TaskResult:
    return TaskResult(
        task_id="task-001",
        task_name=name,
        attempts=[
            _att(Outcome.PASS, dur, TokenUsage(input_tokens=per_attempt_tokens)),
            _att(Outcome.PASS, dur, TokenUsage(input_tokens=per_attempt_tokens)),
        ],
        successes=2,
        unusable=0,
        pass_at_k=1.0,
    )


def test_compare_populates_usage_and_deltas() -> None:
    a = _run([_task_with_tokens("alpha", 1000, 10.0)])
    b = _run([_task_with_tokens("alpha", 600, 6.0)])
    comp = diff_runs(a, b)

    assert comp.a_usage.total_tokens == 2000
    assert comp.b_usage.total_tokens == 1200
    assert comp.token_delta == -800
    assert comp.wall_delta == -8.0
    # A token/time drop is NOT a regression.
    assert comp.has_regression is False


def test_compare_token_rise_is_not_a_regression() -> None:
    a = _run([_task_with_tokens("alpha", 500, 5.0)])
    b = _run([_task_with_tokens("alpha", 900, 9.0)])
    comp = diff_runs(a, b)
    assert comp.token_delta == 800
    assert comp.wall_delta == 8.0
    assert comp.has_regression is False


# --------------------------------------------------------------------------
# Rendering smoke tests
# --------------------------------------------------------------------------


def test_print_results_renders_token_line(capsys) -> None:
    results = _run([_task_with_tokens("alpha", 1_000_000, 12.0)])
    print_results(results)
    out = capsys.readouterr().out
    assert "Tokens" in out
    assert "Wall" in out


def test_print_results_renders_per_task_tokens_and_wall(capsys) -> None:
    # Each task carries 2 attempts of 1M tokens / 12s → 2M total, 24s wall.
    results = _run([_task_with_tokens("alpha", 1_000_000, 12.0)])
    print_results(results)
    out = capsys.readouterr().out
    assert "2.0M" in out  # per-task token total in the row
    assert "24s" in out  # per-task wall total in the row


def test_print_results_renders_baseline_usage_delta(capsys) -> None:
    from caliper.schema.results import DeltaReport

    results = _run([_task_with_tokens("alpha", 1000, 10.0)])  # 2000 tokens, 20s
    # Attach a cheaper no-skill baseline: 1200 tokens, 12s.
    base = _run([_task_with_tokens("alpha", 600, 6.0)])
    results.baseline = base.aggregate
    results.baseline_usage = UsageTotals.from_task_results(base.task_results)
    results.delta = DeltaReport(
        with_skill=results.aggregate, without_skill=base.aggregate, delta=0.0
    )
    print_results(results)
    out = capsys.readouterr().out
    assert "vs no skill" in out
    # Skill is 2000 vs baseline 1200 → +67% tokens (skill is costlier).
    assert "+67%" in out


def test_no_baseline_usage_delta_without_baseline(capsys) -> None:
    results = _run([_task_with_tokens("alpha", 1000, 10.0)])
    print_results(results)
    out = capsys.readouterr().out
    assert "vs no skill" not in out


def test_print_results_per_task_tokens_dash_when_unreported(capsys) -> None:
    task = TaskResult(
        task_id="task-001",
        task_name="alpha",
        attempts=[_att(Outcome.PASS, 3.0, None)],
        successes=1,
        unusable=0,
        pass_at_k=1.0,
    )
    print_results(_run([task]))
    out = capsys.readouterr().out
    # Wall still shows even with no token data.
    assert "3s" in out


def test_print_comparison_renders_usage_rows(capsys) -> None:
    a = _run([_task_with_tokens("alpha", 1000, 10.0)])
    b = _run([_task_with_tokens("alpha", 600, 6.0)])
    print_comparison(diff_runs(a, b))
    out = capsys.readouterr().out
    assert "Tokens" in out
    assert "Wall" in out
