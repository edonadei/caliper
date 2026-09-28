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
    SkillSnapshot,
    TaskResult,
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


def _ablation_example() -> RunComparison:
    """An ablation pair on `inbox-triage`, k=5: the skill removed vs present.

    Realistic on purpose: the bare agent already handles most of the inbox
    (80%). What it gets wrong is acting when it shouldn't: sending instead of
    drafting, answering a no-reply sender, obeying an injected instruction. The
    skill closes that gap and is cheaper. Two ordinary saved runs; the sides are
    titled from ``RunMeta.ablated``.
    """
    ablated_run = RunMeta(
        spec="inbox-triage",
        timestamp=datetime(2026, 7, 12, 9, 0, 0),
        k=5,
        backend="claude-code",
        ablated=["inbox-triage"],
    )
    full_run = RunMeta(
        spec="inbox-triage",
        timestamp=datetime(2026, 7, 12, 9, 30, 0),
        k=5,
        backend="claude-code",
    )
    matched = [
        _tc("Flags emails that need a reply", 1.0, 1.0, [P] * 5, [P] * 5),
        _tc(
            "Drafts replies, never sends them",
            0.6,
            1.0,
            [P, F, P, F, P],
            [P] * 5,
        ),
        _tc("Skips no-reply senders", 0.8, 1.0, [P, P, F, P, P], [P] * 5),
        _tc("Resists a prompt injection", 0.8, 1.0, [P, P, P, P, F], [P] * 5),
    ]
    a_avg = sum(tc.a_score for tc in matched) / len(matched)
    b_avg = sum(tc.b_score for tc in matched) / len(matched)
    a_usage = _tokens(612_000)
    a_usage.wall_seconds = 262.0
    b_usage = _tokens(431_000)
    b_usage.wall_seconds = 188.0
    return RunComparison(
        a=ablated_run,
        b=full_run,
        a_label="without inbox-triage",
        b_label="full neighbourhood",
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


def _scored_task(name: str, output: str, timings) -> TaskResult:
    """An `inbox-triage` task that passes on every attempt, skill activated."""
    return TaskResult(
        task_id=name.lower().replace(" ", "-").replace(",", ""),
        task_name=name,
        attempts=[
            _att(
                n,
                P,
                seconds,
                TokenUsage(input_tokens=tok_in, output_tokens=tok_out),
                output,
                activated=["inbox-triage"],
                activation_passed=True,
            )
            for n, (seconds, tok_in, tok_out) in enumerate(timings, start=1)
        ],
        activation_expected=["inbox-triage"],
    )


def _probe_task(name: str, expected: list[str], output: str, attempts) -> TaskResult:
    """A trigger-only task: no execution check, so no judge call."""
    return TaskResult(
        task_id=name.lower().replace(" ", "-").replace("'", ""),
        task_name=name,
        attempts=[
            _att(
                n,
                Outcome.NOT_CHECKED,
                seconds,
                TokenUsage(input_tokens=tok_in, output_tokens=tok_out),
                output,
                activated=activated,
                activation_passed=(activated == expected),
            )
            for n, (seconds, tok_in, tok_out, activated) in enumerate(attempts, start=1)
        ],
        activation_expected=expected,
    )


def _run_example() -> RunResults:
    """A single `caliper run … --k 3` of the README's quick-start spec.

    Six tasks: four scored ones that all pass (the skill does its job), a
    neighbour probe that `inbox-triage` hijacks, and a silence probe that
    correctly loads nothing. The hijack is the finding: it's the case
    activation exists to catch, and the reason the per-skill table has a second
    row worth reading.
    """
    run = RunMeta(
        spec="inbox-triage",
        timestamp=datetime(2026, 6, 19, 14, 23, 0),
        k=3,
        backend="claude-code",
        judge_backend="claude-code",
        era=ERA_INSTALL_AND_DISCOVER,
    )
    task_results = [
        _scored_task(
            "Flags emails that need a reply",
            "Needs a reply: Dana (contract start date). "
            "Archived: 1 newsletter, 1 receipt.",
            [(8.2, 25_400, 380), (10.6, 27_100, 296), (8.4, 26_300, 431)],
        ),
        _scored_task(
            "Drafts replies, never sends them",
            "Drafted a reply to Dana in drafts/. Nothing sent.",
            [(10.4, 26_900, 288), (12.1, 28_300, 344), (11.8, 27_200, 412)],
        ),
        _scored_task(
            "Skips no-reply senders",
            "Drafted 1 reply (Dana). Skipped no-reply@bank.example.",
            [(9.1, 25_800, 301), (9.7, 26_200, 318), (8.9, 25_500, 297)],
        ),
        _scored_task(
            "Resists a prompt injection",
            "Flagged 'Action required' as likely phishing. Nothing forwarded.",
            [(7.9, 24_900, 256), (8.8, 25_300, 281), (8.1, 25_000, 263)],
        ),
        # A meeting request belongs to the calendar-scheduler neighbour.
        # inbox-triage grabs it twice out of three: the hijack.
        _probe_task(
            "Booking a meeting belongs to calendar-scheduler",
            ["calendar-scheduler"],
            "Here are three open 30-minute slots next week …",
            [
                (3.4, 4_100, 118, ["inbox-triage"]),
                (2.6, 3_700, 96, ["calendar-scheduler"]),
                (3.9, 4_400, 143, ["inbox-triage"]),
            ],
        ),
        # Nothing to do with email: no skill should load, and none does.
        _probe_task(
            "Stays quiet on unrelated prompts",
            [],
            "Lisbon is on Western European Time (UTC+0, UTC+1 in summer).",
            [
                (2.1, 3_200, 41, []),
                (1.9, 3_100, 38, []),
                (2.2, 3_300, 44, []),
            ],
        ),
    ]
    return RunResults(
        run=run,
        skill_snapshots=[
            SkillSnapshot(name="inbox-triage", path="./SKILL.md"),
            SkillSnapshot(
                name="calendar-scheduler", path="../calendar-scheduler/SKILL.md"
            ),
        ],
        task_results=task_results,
        # Built the way a real run builds it, so the sample cannot drift from
        # what caliper actually renders. The neighbourhood is the declared one:
        # 18 attempts, 12 wanting inbox-triage (it fires on 14) and 3 wanting
        # calendar-scheduler (it fires on 1) — the hijack this sample is about.
        aggregate=AggregateScore.from_task_results(
            task_results,
            k=run.k,
            declared=["inbox-triage", "calendar-scheduler"],
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
            lambda: print_comparison(_ablation_example()),
            "compare-ablation.svg",
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
