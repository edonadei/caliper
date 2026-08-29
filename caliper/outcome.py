from __future__ import annotations

import re

from caliper.harness.base import AttemptResult
from caliper.judge.base import JudgeResult
from caliper.schema.results import Outcome

# Mid-run harness signals that mean the skill was never fairly run. Matched over
# the attempt's output + error even on a zero exit, because these typically let
# the CLI exit 0 with the message as its only "output". Startup auth / login
# misconfiguration is deliberately NOT here: that raises
# HarnessConfigurationError and aborts the whole run instead.
#
# Split in two because the right response differs, not because the labels do —
# both still earn INFRA_ERROR when they reach the end of the line. The question
# each half answers is "would another invocation behave differently?"

# Yes: the provider is busy or we are going too fast. Worth retrying in seconds
# (docs/adr/0019-an-attempt-may-be-invoked-more-than-once.md).
_THROTTLE_SIGNALS = re.compile(
    r"rate.?limit|\b429\b|overloaded|too many requests|service unavailable|\b503\b",
    re.IGNORECASE,
)

# No: the account is out of budget, and every remaining attempt of the run would
# meet the same wall. Aborts the run rather than being retried or repeated.
_CAP_SIGNALS = re.compile(
    r"spending cap|quota (?:exceeded|reached)|usage limit",
    re.IGNORECASE,
)


def answered(result: AttemptResult) -> bool:
    """Whether the agent actually produced an answer, rather than the CLI talking.

    The discriminator a bare regex over the output cannot supply. Signals are
    matched even on a zero exit, because a capped CLI exits 0 with the cap
    message as its only output — but an agent *answering* a task about API error
    handling writes "rate limit" and "quota exceeded" too, and treating that as a
    provider signal mislabels a passing attempt (and, at the retry seam, would
    respawn it or abort the run).

    The tell is whether the backend's stream parser produced anything: a real run
    is a conversation, while a CLI that bailed parses to nothing and survives only
    via the raw-stdout salvage. Length is deliberately not the test — "Done,
    added 429 handling." is a short genuine answer.
    """
    return (
        result.exit_code == 0
        and not result.timed_out
        and bool(result.transcript)
        and not result.salvaged
    )


def looks_like_throttle(text: str) -> bool:
    """True when free text says the provider is busy — a retryable signal."""
    return bool(text) and bool(_THROTTLE_SIGNALS.search(text))


def looks_like_spending_cap(text: str) -> bool:
    """True when free text says the account is out of budget.

    Not retryable and not survivable: the cap does not clear inside a run, so
    the runner aborts rather than spending every remaining attempt discovering
    the same wall. See docs/adr/0019.
    """
    return bool(text) and bool(_CAP_SIGNALS.search(text))


def looks_like_infra_failure(text: str) -> bool:
    """True when free text carries a transient throttle/overload signal.

    The single source of truth for infra detection, shared by the runner (to
    decide whether to skip a paid judge call) and ``classify_outcome`` (to label
    the attempt), so the two can never disagree about what counts as noise. The
    union of both halves above: how an attempt is *labelled* is unchanged by the
    split, which only decides what the runner does before the label is reached.
    """
    return looks_like_throttle(text) or looks_like_spending_cap(text)


def signal_text(result: AttemptResult) -> str:
    """The text a provider signal could be hiding in: the output plus the error.

    One definition, used by the label (``classify_pre_judge``) and by the retry
    seam, so the two can never disagree about *where* they looked.
    """
    return "\n".join(part for part in (result.final_output, result.error) if part)


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

    if harness.exit_code != 0:
        return Outcome.INFRA_ERROR

    # Zero exit: a provider signal here is only real if the agent never answered.
    # Otherwise this is an attempt that *passed* while writing about rate limits
    # — which, for a tool that evaluates skills, is an ordinary task.
    if not answered(harness) and looks_like_infra_failure(signal_text(harness)):
        return Outcome.INFRA_ERROR

    return None


def classify_outcome(
    harness: AttemptResult,
    cheat_violations: list[str],
    judge: JudgeResult | None,
    *,
    has_execution_check: bool = True,
) -> Outcome:
    """Map an attempt's harness result, cheat violations, and judge result to an Outcome.

    The single seam: every attempt is labelled here and nowhere else. Precedence
    is timeout -> infra_error -> cheat -> not_checked -> judge_error -> judge
    verdict, so infrastructure noise always wins over a (garbage) judge verdict,
    and a real task failure is only ever reported when the attempt actually
    reached a judge that produced a verdict.

    ``has_execution_check`` is ``False`` for an ``activates:``-only task, which
    authored no ``expect:``/``assert:``. That earns ``NOT_CHECKED``, never
    ``JUDGE_ERROR``: nothing was asked of the judge, so its silence is the right
    answer rather than a fault. It ranks *below* cheat so a forbidden-file read
    is still caught on a trigger probe.

    ``judge`` is ``None`` on the early-exit paths (timeout / infra / cheat /
    not_checked) where the judge was never run.
    """
    pre_judge = classify_pre_judge(harness)
    if pre_judge is not None:
        return pre_judge

    if cheat_violations:
        return Outcome.CHEAT

    if not has_execution_check:
        return Outcome.NOT_CHECKED

    if judge is None or judge.errored:
        return Outcome.JUDGE_ERROR

    return Outcome.PASS if judge.passed else Outcome.TASK_FAIL
