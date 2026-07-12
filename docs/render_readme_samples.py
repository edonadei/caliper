#!/usr/bin/env python3
"""Render the README's `caliper compare` example outputs to SVG.

The README shows sample terminal output. Hand-drawn ASCII-box tables drift out
of alignment in any renderer that draws the ambiguous-width glyphs (✓ ✗ ⊘ → Δ)
wider than one cell, which is font-dependent — so the same block looks broken on
some screens and fine on others. Instead we render the *real* reporter output
(caliper.reporter.print_comparison) into a recording rich Console and export it
as SVG: a vector image that looks like a terminal and is pixel-identical
everywhere, because it no longer depends on the reader's font.

These SVGs are committed and embedded in README.md. Regenerate them whenever the
compare view changes:

    python docs/render_readme_samples.py

The two comparisons below are illustrative fixtures, not real runs; they exist
only to reproduce the numbers the README prose explains. Keep them in sync with
that prose.
"""

from __future__ import annotations

import io
from datetime import datetime
from pathlib import Path

from rich.console import Console

import caliper.reporter as reporter
from caliper.reporter import print_comparison
from caliper.schema.results import (
    Outcome,
    RunComparison,
    RunMeta,
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


def _render(comp: RunComparison, out_name: str, title: str) -> Path:
    """Drive the real reporter into a recording console and export SVG."""
    rec = Console(record=True, width=_WIDTH, file=io.StringIO())
    original = reporter.console
    reporter.console = rec
    try:
        print_comparison(comp)
    finally:
        reporter.console = original
    _ASSETS.mkdir(parents=True, exist_ok=True)
    out = _ASSETS / out_name
    out.write_text(rec.export_svg(title=title))
    return out


def main() -> None:
    for path in (
        _render(_baseline_example(), "compare-baseline.svg", "caliper compare"),
        _render(_compare_example(), "compare-runs.svg", "caliper compare"),
    ):
        print(f"wrote {path}")


if __name__ == "__main__":
    main()
