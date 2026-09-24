from __future__ import annotations

import json
import re
from dataclasses import dataclass

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


def no_model_call_observed(result: AttemptResult) -> bool:
    """Whether nothing shows the agent ever got as far as a model call.

    No parsed answer or agent turn *and* no tokens reported: typically the CLI
    bailed on its own (an expired login reported inside a zero-exit stream,
    #132) and whatever it printed is not an answer. Both halves, because either
    alone is ambiguous — a parser can miss a stream the model did produce
    (tokens say it ran, and the salvaged stdout is still its answer), and a
    backend may not report usage at all (a parsed conversation says it ran).

    "Observed", not "made": a backend that reads both from a step after the
    agent (hermes' session export) can lose the evidence of a call that did
    happen. Either way there is nothing to judge. See
    docs/adr/0001-attempt-outcome-taxonomy.md.
    """
    # A parsed answer is the agent speaking; the prompt a backend echoes back
    # (hermes' export) is input, not a call.
    parsed = not result.salvaged and (
        bool(result.final_output) or any(t.role != "user" for t in result.transcript)
    )
    tokens = result.usage.total_tokens if result.usage is not None else None
    return not parsed and not tokens


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


def spending_cap_line(text: str) -> str:
    """The line of ``text`` that says the account is out of budget.

    What the abort quotes. A CLI's first line is usually its own chatter (codex
    opens with ``thread.started``), while the line that matched is the one that
    says when the limit resets.
    """
    lines = text.strip().splitlines()
    line = next((line for line in lines if _CAP_SIGNALS.search(line)), lines[0])
    # A JSON event line (codex's `{"type":"error","message":…}`) is quoted by
    # its message, so the reset time survives the length cap.
    try:
        event = json.loads(line)
    except ValueError:
        return line
    message = event.get("message") if isinstance(event, dict) else None
    return message if isinstance(message, str) and message else line


def looks_like_infra_failure(text: str) -> bool:
    """True when free text carries a transient throttle/overload signal.

    The single source of truth for infra detection, used by ``classify_pre_judge``
    to label the attempt. The union of both halves above: how an attempt is
    *labelled* is unchanged by the split, which only decides what the retry seam
    does before the label is reached.
    """
    return looks_like_throttle(text) or looks_like_spending_cap(text)


def signal_text(result: AttemptResult) -> str:
    """The text a provider signal could be hiding in: the output plus the error.

    One definition, used by the label (``classify_pre_judge``) and by the retry
    seam, so the two can never disagree about *where* they looked.
    """
    return "\n".join(part for part in (result.final_output, result.error) if part)


@dataclass(frozen=True)
class PreJudgeExit:
    """An attempt that ends before the judge: its label and why.

    The reason travels with the label so the evidence a record carries is the
    branch that fired, never a second guess at it.
    """

    outcome: Outcome
    evidence: str


def classify_pre_judge(harness: AttemptResult) -> PreJudgeExit | None:
    """The terminal outcome an attempt earns from its harness result alone.

    Returns a ``TIMEOUT`` or ``INFRA_ERROR`` exit when the attempt never got a
    fair shot, so it must skip cheat detection and the (paid) judge entirely;
    returns ``None`` when the attempt ran cleanly enough to proceed.
    ``assemble_attempt`` both skips on it and records it as the label, so the
    two cannot disagree. The harness's own error, when it reported one, is the
    evidence; otherwise the branch says what it saw.
    """

    def ends(outcome: Outcome, seen: str) -> PreJudgeExit:
        return PreJudgeExit(outcome, harness.error or seen)

    exited = f"harness exited {harness.exit_code}"
    if harness.timed_out:
        return ends(Outcome.TIMEOUT, exited)

    if harness.exit_code != 0:
        return ends(Outcome.INFRA_ERROR, exited)

    # Zero exit, but no model call was ever seen: there is no attempt to judge,
    # and an activation check would read "nothing fired" as a verdict on the
    # skill rather than on a CLI that never started (docs/adr/0001).
    if no_model_call_observed(harness):
        return ends(
            Outcome.INFRA_ERROR,
            "no model call observed: nothing parsed from the agent's stream "
            "and no tokens reported",
        )

    # Zero exit: a provider signal here is only real if the agent never answered.
    # Otherwise this is an attempt that *passed* while writing about rate limits
    # — which, for a tool that evaluates skills, is an ordinary task.
    if not answered(harness) and looks_like_infra_failure(signal_text(harness)):
        return ends(Outcome.INFRA_ERROR, exited)

    return None


def judge_outcome(judge: JudgeResult) -> Outcome:
    """The outcome an attempt earns from the verdict the judge returned.

    ``JUDGE_ERROR`` when the judge produced no usable verdict; otherwise the
    verdict itself. The last step of the precedence ``assemble_attempt`` walks
    (timeout → infra_error → cheat → not_checked → judge), reached only by an
    attempt that got a fair shot and had a check to grade.
    """
    if judge.errored:
        return Outcome.JUDGE_ERROR
    return Outcome.PASS if judge.passed else Outcome.TASK_FAIL
