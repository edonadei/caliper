from __future__ import annotations

from dataclasses import dataclass

from caliper.harness.base import AttemptResult
from caliper.judge.base import JudgeResult
from caliper.schema.results import Outcome


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

    # Zero exit, but the CLI said the provider refused: a throttle that
    # outlasted its retries. Read from what the CLI wrote, so an attempt that
    # passed while writing about rate limits is not one (docs/adr/0030).
    if harness.refusal is not None:
        return ends(Outcome.INFRA_ERROR, harness.refusal.message)

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
