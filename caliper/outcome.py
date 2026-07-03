from __future__ import annotations

import re

from caliper.harness.base import AttemptResult
from caliper.judge.base import JudgeResult
from caliper.schema.results import Outcome

# Transient, mid-run harness signals that mean the skill was never fairly run —
# a spending cap, a rate limit, an overloaded provider. Matched over the
# attempt's output + error even on a zero exit, because a spending cap typically
# lets the CLI exit 0 with the cap message as its only "output". Startup auth /
# login misconfiguration is deliberately NOT here: that raises
# HarnessConfigurationError and aborts the whole run instead.
_INFRA_SIGNALS = re.compile(
    r"spending cap|rate.?limit|\b429\b|overloaded|quota (?:exceeded|reached)"
    r"|usage limit|too many requests|service unavailable|\b503\b",
    re.IGNORECASE,
)


def looks_like_infra_failure(text: str) -> bool:
    """True when free text carries a transient throttle/overload signal.

    The single source of truth for infra detection, shared by the runner (to
    decide whether to skip a paid judge call) and ``classify_outcome`` (to label
    the attempt), so the two can never disagree about what counts as noise.
    """
    return bool(text) and bool(_INFRA_SIGNALS.search(text))


def classify_pre_judge(harness: AttemptResult) -> Outcome | None:
    """The terminal outcome an attempt earns from its harness result alone.

    Returns ``TIMEOUT`` or ``INFRA_ERROR`` when the attempt never got a fair
    shot, so it must skip cheat detection and the (paid) judge entirely; returns
    ``None`` when the attempt ran cleanly enough to proceed. This is the single
    authority on that predicate: the runner asks it to decide whether to spend a
    judge call, and ``classify_outcome`` reuses it for the final label, so the
    skip and the label can never disagree.
    """
    if harness.timed_out:
        return Outcome.TIMEOUT

    text = "\n".join(part for part in (harness.final_output, harness.error) if part)
    if harness.exit_code != 0 or looks_like_infra_failure(text):
        return Outcome.INFRA_ERROR

    return None


def classify_outcome(
    harness: AttemptResult,
    cheat_violations: list[str],
    judge: JudgeResult | None,
) -> Outcome:
    """Map an attempt's harness result, cheat violations, and judge result to an Outcome.

    The single seam: every attempt is labelled here and nowhere else. Precedence
    is timeout -> infra_error -> cheat -> judge_error -> judge verdict, so
    infrastructure noise always wins over a (garbage) judge verdict, and a real
    task failure is only ever reported when the attempt actually reached a
    judge that produced a verdict.

    ``judge`` is ``None`` on the early-exit paths (timeout / infra / cheat) where
    the judge was never run.
    """
    pre_judge = classify_pre_judge(harness)
    if pre_judge is not None:
        return pre_judge

    if cheat_violations:
        return Outcome.CHEAT

    if judge is None or judge.errored:
        return Outcome.JUDGE_ERROR

    return Outcome.PASS if judge.passed else Outcome.TASK_FAIL
