#!/usr/bin/env python3
"""Render the README's sample terminal outputs to SVG.

The README shows sample terminal output. Hand-drawn ASCII-box tables drift out
of alignment in any renderer that draws the ambiguous-width glyphs (✓ ✗ ⊘ → Δ)
wider than one cell, which is font-dependent — so the same block looks broken on
some screens and fine on others. Instead we render the *real* reporter output
(caliper.reporter.print_results / print_comparison) into a recording rich Console
and export it as SVG: a vector image that looks like a terminal and is
pixel-identical everywhere, because it no longer depends on the reader's font.

These SVGs are committed and embedded in README.md. Regenerate them whenever the
run or compare views change:

    python docs/render_readme_samples.py

The fixtures below are illustrative, not real runs; they exist only to reproduce
the numbers the README prose explains. Keep them in sync with that prose.
"""

from __future__ import annotations

import io
from datetime import datetime
from pathlib import Path

from rich.console import Console

import caliper.reporter as reporter
from caliper.reporter import print_comparison, print_results
from caliper.schema.results import (
    AggregateScore,
    AttemptRecord,
    Outcome,
    RunComparison,
    RunMeta,
    RunResults,
    SkillSnapshot,
    TaskResult,
    TaskScore,
    TokenUsage,
    UsageTotals,
)

# Terminal-emulator width for the exported SVG. Wide enough for the longest
# header line (the two ISO timestamps + engine in the plain-compare example)
# without wrapping.
_WIDTH = 92

_ASSETS = Path(__file__).resolve().parent / "assets"

P = Outcome.PASS
F = Outcome.TASK_FAIL
E = Outcome.INFRA_ERROR


def _tokens(total: int) -> UsageTotals:
    """A usage roll-up whose only reported figure is a round token total."""
    return UsageTotals(input_tokens=total, tokens_reported=True)


def _tc(name, a_score, b_score, a_outcomes, b_outcomes):
    from caliper.schema.results import TaskComparison

    both = a_score is not None and b_score is not None
    return TaskComparison(
        task_name=name,
        a_score=a_score,
        b_score=b_score,
        delta=(b_score - a_score) if both else None,
        regression=both and b_score < a_score,
        a_outcomes=a_outcomes,
        b_outcomes=b_outcomes,
    )


def _baseline_example() -> RunComparison:
    """`--baseline` diff: no skill vs with skill on `commit-commands`, k=3."""
    run = RunMeta(
        spec="commit-commands",
        timestamp=datetime(2026, 7, 12, 9, 0, 0),
        k=3,
        backend="claude-code",
    )
    matched = [
        _tc("Commits a new feature", 1 / 3, 1.0, [P, F, F], [P, P, P]),
        _tc("Commits a bug fix", 1 / 3, 1.0, [F, P, F], [P, P, P]),
    ]
    a_avg = sum(tc.a_score for tc in matched) / len(matched)
    b_avg = sum(tc.b_score for tc in matched) / len(matched)
    a_usage = _tokens(290_000)
    a_usage.wall_seconds = 61.0
    b_usage = _tokens(180_000)
    b_usage.wall_seconds = 42.0
    return RunComparison(
        a=run,
        b=run,
        a_label="no skill",
        b_label="with skill",
        matched=matched,
        unmatched_a=[],
        unmatched_b=[],
        a_matched_avg=a_avg,
        b_matched_avg=b_avg,
        aggregate_delta=b_avg - a_avg,
        has_regression=False,
        k_mismatch=False,
        spec_mismatch=False,
        warnings=[],
        a_usage=a_usage,
        b_usage=b_usage,
    )


def _compare_example() -> RunComparison:
    """Plain `caliper compare A B` of two saved `commit-simple` runs, k=5, with a
    regression, an unmeasured task, and unmatched tasks on each side."""
    a_run = RunMeta(
        spec="commit-simple",
        timestamp=datetime(2026, 7, 1, 10, 0, 0),
        k=5,
        backend="claude-code",
    )
    b_run = RunMeta(
        spec="commit-simple",
        timestamp=datetime(2026, 7, 2, 9, 0, 0),
        k=5,
        backend="claude-code",
    )
    matched = [
        _tc("commits cleanly", 1.0, 1.0, [P] * 5, [P] * 5),
        _tc("handles conflict", 1.0, 0.2, [P] * 5, [P, F, F, F, F]),
        _tc("pushes upstream", 0.8, None, [P, P, P, P, F], [E] * 5),
    ]
    comparable = [
        tc for tc in matched if tc.a_score is not None and tc.b_score is not None
    ]
    a_avg = sum(tc.a_score for tc in comparable) / len(comparable)
    b_avg = sum(tc.b_score for tc in comparable) / len(comparable)
    a_usage = _tokens(1_200_000)
    a_usage.wall_seconds = 378.0
    b_usage = _tokens(700_000)
    b_usage.wall_seconds = 220.0
    return RunComparison(
        a=a_run,
        b=b_run,
        a_label=None,
        b_label=None,
        matched=matched,
        unmatched_a=["flaky task"],
        unmatched_b=["new task"],
        a_matched_avg=a_avg,
        b_matched_avg=b_avg,
        aggregate_delta=b_avg - a_avg,
        has_regression=True,
        k_mismatch=False,
        spec_mismatch=False,
        warnings=[],
        a_usage=a_usage,
        b_usage=b_usage,
    )


def _att(
    attempt: int,
    outcome: Outcome,
    seconds: float,
    tokens: TokenUsage,
    output: str,
    assert_evidence: str | None = None,
) -> AttemptRecord:
    return AttemptRecord(
        attempt=attempt,
        output=output,
        duration_seconds=seconds,
        outcome=outcome,
        usage=tokens,
        assert_passed=None if assert_evidence is None else outcome is P,
        assert_evidence=assert_evidence,
    )


def _run_example() -> RunResults:
    """A single `caliper run … --k 3` of the two tasks in the README's spec: a
    clean-passing autorater task and a script-assertion task that fails once (so
    the report shows a PASS row, a PARTIAL row, and the failure panel that
    explains *why*). Token/wall figures are chosen to sum to the summary line."""
    run = RunMeta(
        spec="my-skill",
        timestamp=datetime(2026, 6, 19, 14, 23, 0),
        k=3,
        backend="claude-code",
        judge_backend="claude-code",
    )
    # 26_000 in + 350 out per attempt → 79K over three; 9s each → 27s.
    commit_usage = TokenUsage(input_tokens=26_000, output_tokens=350)
    commit = TaskResult(
        task_id="writes-a-conventional-commit-message",
        task_name="Writes a conventional commit message",
        attempts=[
            _att(i, P, 9.0, commit_usage, "feat(auth): add token refresh\n\n…")
            for i in (1, 2, 3)
        ],
        successes=3,
        unusable=0,
        pass_at_k=1.0,
    )
    # 27_000 in + 350 out per attempt → 82K over three; 11s each → 33s.
    config_usage = TokenUsage(input_tokens=27_000, output_tokens=350)
    config = TaskResult(
        task_id="generates-a-valid-config-file",
        task_name="Generates a valid config file",
        attempts=[
            _att(1, P, 11.0, config_usage, "Wrote /tmp/app.config.json"),
            _att(2, P, 11.0, config_usage, "Wrote /tmp/app.config.json"),
            _att(
                3,
                F,
                11.0,
                config_usage,
                "Wrote /tmp/app.config.json",
                assert_evidence="AssertionError: data['port'] == 8080 (got 3000)",
            ),
        ],
        successes=2,
        unusable=0,
        pass_at_k=1.0,
    )
    task_results = [commit, config]
    return RunResults(
        run=run,
        skill_snapshot=SkillSnapshot(path="./SKILL.md"),
        task_results=task_results,
        aggregate=AggregateScore(
            avg_score=sum(tr.score for tr in task_results) / len(task_results),
            per_task=[
                TaskScore(
                    task_id=tr.task_id,
                    task_name=tr.task_name,
                    k=run.k,
                    successes=tr.successes,
                    score=tr.score,
                )
                for tr in task_results
            ],
        ),
    )


def _record_svg(render, out_name: str, title: str) -> Path:
    """Drive the real reporter into a recording console and export SVG."""
    rec = Console(record=True, width=_WIDTH, file=io.StringIO())
    original = reporter.console
    reporter.console = rec
    try:
        render()
    finally:
        reporter.console = original
    _ASSETS.mkdir(parents=True, exist_ok=True)
    out = _ASSETS / out_name
    out.write_text(rec.export_svg(title=title))
    return out


def main() -> None:
    for path in (
        _record_svg(
            lambda: print_comparison(_baseline_example()),
            "compare-baseline.svg",
            "caliper compare",
        ),
        _record_svg(
            lambda: print_comparison(_compare_example()),
            "compare-runs.svg",
            "caliper compare",
        ),
        _record_svg(
            lambda: print_results(_run_example()),
            "run-output.svg",
            "caliper run",
        ),
    ):
        print(f"wrote {path}")


if __name__ == "__main__":
    main()
