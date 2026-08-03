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
    ERA_INSTALL_AND_DISCOVER,
    AggregateScore,
    AttemptRecord,
    Outcome,
    RunComparison,
    RunMeta,
    RunResults,
    SkillActivationStats,
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
    activated: list[str] | None = None,
    activation_passed: bool | None = None,
) -> AttemptRecord:
    return AttemptRecord(
        attempt=attempt,
        output=output,
        duration_seconds=seconds,
        outcome=outcome,
        usage=tokens,
        assert_passed=None if assert_evidence is None else outcome is P,
        assert_evidence=assert_evidence,
        activated=activated,
        activation_passed=activation_passed,
    )


def _run_example() -> RunResults:
    """A single `caliper run … --k 3` of the README's quick-start spec.

    Three tasks, one per kind of check: an autorater task that passes cleanly, a
    script-assertion task that fails once (so the report shows a PASS row, a
    PARTIAL row, and the failure panel explaining *why*), and a neighbour probe
    that `commit-writer` hijacks — the case activation exists to catch, and the
    reason the per-skill table has a second row worth reading.
    """
    run = RunMeta(
        spec="commit-writer",
        timestamp=datetime(2026, 6, 19, 14, 23, 0),
        k=3,
        backend="claude-code",
        judge_backend="claude-code",
        era=ERA_INSTALL_AND_DISCOVER,
    )
    # 26_000 in + 350 out per attempt → 79K over three; 9s each → 27s.
    message_usage = TokenUsage(input_tokens=26_000, output_tokens=350)
    message = TaskResult(
        task_id="writes-a-conventional-commit-message",
        task_name="Writes a conventional commit message",
        attempts=[
            _att(
                i,
                P,
                9.0,
                message_usage,
                "feat(auth): add token refresh\n\n…",
                activated=["commit-writer"],
                activation_passed=True,
            )
            for i in (1, 2, 3)
        ],
        successes=3,
        unusable=0,
        pass_at_k=1.0,
        activation_expected=["commit-writer"],
    )
    # 27_000 in + 350 out per attempt → 82K over three; 11s each → 33s.
    subject_usage = TokenUsage(input_tokens=27_000, output_tokens=350)
    subject = TaskResult(
        task_id="keeps-the-subject-line-under-72-characters",
        task_name="Keeps the subject line under 72 characters",
        attempts=[
            _att(
                n,
                outcome,
                11.0,
                subject_usage,
                "Committed as feat(api): paginate the search endpoint",
                assert_evidence=evidence,
                activated=["commit-writer"],
                activation_passed=True,
            )
            for n, outcome, evidence in (
                (1, P, None),
                (2, P, None),
                (3, F, "AssertionError: subject line is 94 chars (limit 72)"),
            )
        ],
        successes=2,
        unusable=0,
        pass_at_k=1.0,
        activation_expected=["commit-writer"],
    )
    # A release-notes request belongs to the changelog-writer neighbour. Cheap:
    # no execution check means no judge call. 4_000 in + 100 out, 3s each.
    probe_usage = TokenUsage(input_tokens=4_000, output_tokens=100)
    probe = TaskResult(
        task_id="a-release-summary-belongs-to-changelog-writer",
        task_name="A release summary belongs to changelog-writer",
        attempts=[
            _att(
                n,
                Outcome.NOT_CHECKED,
                3.0,
                probe_usage,
                "Here is a summary of the changes since v2.1 …",
                activated=activated,
                activation_passed=(activated == ["changelog-writer"]),
            )
            # commit-writer grabs it twice out of three: the hijack.
            for n, activated in (
                (1, ["commit-writer"]),
                (2, ["changelog-writer"]),
                (3, ["commit-writer"]),
            )
        ],
        successes=0,
        unusable=0,
        pass_at_k=None,
        activation_expected=["changelog-writer"],
    )
    task_results = [message, subject, probe]
    scored = [tr for tr in task_results if tr.score is not None]
    return RunResults(
        run=run,
        skill_snapshots=[
            SkillSnapshot(name="commit-writer", path="./SKILL.md"),
            SkillSnapshot(name="changelog-writer", path="../changelog-writer/SKILL.md"),
        ],
        task_results=task_results,
        aggregate=AggregateScore(
            avg_score=sum(tr.score for tr in scored) / len(scored),
            scored_tasks=len(scored),
            avg_activation_score=sum(
                tr.activation_score
                for tr in task_results
                if tr.activation_score is not None
            )
            / 3,
            activation_tasks=3,
            activation_per_skill=[
                # 9 scored attempts: 6 wanted commit-writer (all fired), 3 did
                # not (it fired on 2 of them).
                SkillActivationStats(
                    skill="commit-writer", total=9, expected=6, fired=8, hits=6
                ),
                SkillActivationStats(
                    skill="changelog-writer", total=9, expected=3, fired=1, hits=1
                ),
            ],
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
