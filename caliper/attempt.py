"""Assembling one attempt's record from a finished harness run.

This is **the seam where an attempt is assembled** (docs/CONTEXT.md → Outcome):
given how an attempt ended — a setup hook that failed, or the ``AttemptResult``
the harness produced — decide what happened and return the ``AttemptRecord``
that goes into the results file, or nothing when the run's own cancellation
killed the agent. Every grading rule lives here — the setup-failure and
killed-agent exits, the pre-judge skip, activation, cheat detection, the (paid)
judge call, and the precedence between them. A run that stops before or between
steps records nothing, and that stays with the runner.

Deliberately **pure over an already-produced result**: no threads, no temp
directories, no subprocesses. Those belong to the runner, which owns an
attempt's *lifecycle* while this module owns its *verdict*. That split is what
lets the rules most likely to change be tested without standing up a fake
harness and a thread pool.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from caliper.activation import ActivationDetector, check_activation
from caliper.harness.base import AttemptResult, ConversationTurn
from caliper.judge.base import Judge, JudgeResult
from caliper.sandbox import Sandbox
from caliper.schema.results import AttemptRecord, Outcome, TranscriptTurn
from caliper.schema.spec import TaskSpec
from caliper.workdir import AttemptWorkdir


@dataclass(frozen=True)
class SetupFailed:
    """An attempt whose ``setup:`` hook failed, so the agent never ran.

    ``reason`` is what the hook did — "setup exited 7", "setup timed out after
    60s" — and becomes the record's evidence (docs/adr/0029).
    """

    reason: str


@dataclass(frozen=True)
class AssembledAttempt:
    """One attempt's record, plus what the assembly observed about the run.

    ``judge_model``, ``resolved_model`` and ``loaded_user_customizations`` are
    *run*-level facts (they land in ``RunMeta``): the autorater's concrete
    model, and the agent's model and user customizations as the backend
    reported them. They ride out here rather than on the record — returned
    rather than written into the run's collector, so this module stays pure and
    the runner keeps ownership of what it accumulates across attempts. Deciding
    them here is what keeps an attempt the cancellation discarded from voting.
    """

    record: AttemptRecord
    judge_model: str | None = None
    resolved_model: str | None = None
    loaded_user_customizations: list[str] | None = None


def assemble_attempt(
    ended: AttemptResult | SetupFailed,
    *,
    attempt: int,
    task: TaskSpec,
    workdir: AttemptWorkdir,
    expected_activation: list[str] | None,
    activation: ActivationDetector,
    sandbox: Sandbox,
    judge: Judge,
    retries: int = 0,
) -> AssembledAttempt | None:
    """Grade how one attempt ended into an ``AttemptRecord``.

    The one place an outcome is decided (docs/adr/0001). Precedence: a failed
    setup hook, then a cancelled spawn, then timeout / infra error, then cheat,
    then a missing execution check, then the judge's verdict. Each exit before
    the last skips the judge, so an attempt that never got a fair shot never
    spends a paid autorater call on garbage output.

    ``None`` when the run's cancellation killed the agent: not an observation
    about the skill, so it is no record at all rather than an ``infra_error``.
    Only the spawn knows who killed it, and it says so on the result.

    ``expected_activation`` is what this run asserts the task should activate —
    ``None`` when a skill was ablated, which drops the expectation but keeps the
    observation (docs/adr/0015-ablation-names-its-subject-at-the-invocation.md);
    removing an MCP server leaves it set (docs/adr/0025).

    ``attempt`` is the caller's own counter rather than ``result.attempt``: the
    runner decides which of the k attempts this is, and the harness only echoes
    it back, so the record is numbered from the authority instead of the echo.
    """
    # The agent never ran, so there is nothing to observe or judge.
    if isinstance(ended, SetupFailed):
        return AssembledAttempt(
            record=AttemptRecord(
                attempt=attempt,
                output="",
                duration_seconds=0.0,
                outcome=Outcome.INFRA_ERROR,
                assert_evidence=ended.reason,
            )
        )
    result = ended
    if result.cancelled:
        return None

    # A timeout or infrastructure signal terminates the attempt before we spend
    # a (paid) judge call on garbage output. The pre-judge classifier owns that
    # predicate — nothing re-derives it — so the skip here and the final label
    # can never drift apart.
    pre_judge = _classify_pre_judge(result)

    # A timeout or infra failure can cut the transcript off partway. A skill we
    # saw load before the cut still loaded, so keep it. Seeing nothing is
    # ambiguous (nothing loaded, or we missed it), so record ``None``, not an
    # empty list. Neither is graded. See docs/CONTEXT.md → Activation admissibility.
    observed = activation.detect(
        result.transcript,
        additional_names=result.user_skill_names,
        additional_paths=result.user_skill_paths,
    )
    if pre_judge is None:
        activated = observed
        activation_passed = check_activation(activated, expected_activation)
    else:
        activated = observed or None
        activation_passed = None

    def with_outcome(
        outcome: Outcome,
        judge_model: str | None = None,
        judge_seconds: float | None = None,
        **verdict,
    ) -> AssembledAttempt:
        """One attempt's record; only the verdict fields differ per exit path.

        ``activated``/``activation_passed`` ride on every path, because
        activation is observed from the transcript and owes nothing to the judge.
        """
        return AssembledAttempt(
            record=AttemptRecord(
                attempt=attempt,
                output=result.final_output,
                duration_seconds=result.duration_seconds,
                outcome=outcome,
                usage=result.usage,
                transcript=_persist_transcript(result.transcript),
                activated=activated,
                activation_passed=activation_passed,
                judge_seconds=judge_seconds,
                # A lifecycle fact the runner hands in: how many invocations it
                # took to produce this one result. Nothing here re-derives it.
                retries=retries,
                **verdict,
            ),
            judge_model=judge_model,
            resolved_model=result.resolved_model,
            loaded_user_customizations=result.loaded_user_customizations,
        )

    if pre_judge is not None:
        return with_outcome(pre_judge.outcome, assert_evidence=pre_judge.evidence)

    cheat_violations = sandbox.violations(result.transcript)
    if cheat_violations:
        return with_outcome(Outcome.CHEAT, cheat_evidence=cheat_violations)

    # An `activates:`-only task authored no execution check, so there is nothing
    # to grade — skip the (paid) judge call rather than spending it to receive a
    # non-verdict and label the attempt an error. Ranks below cheat so a
    # forbidden-file read is still caught on a trigger probe.
    if not (task.expect or task.assert_script):
        return with_outcome(Outcome.NOT_CHECKED)

    judge_result = judge.evaluate(
        task=task,
        transcript=result.transcript,
        final_output=result.final_output,
        workdir=workdir,
    )
    return with_outcome(
        _judge_outcome(judge_result),
        judge_model=judge_result.resolved_model,
        # The judge times its own autorater: only a model call is judge time
        # (docs/CONTEXT.md → Judge time), and every earlier exit above leaves
        # it None, which is the difference between "the judge was fast" and
        # "no judge ran".
        judge_seconds=judge_result.autorater_seconds,
        assert_passed=judge_result.assert_passed,
        assert_evidence=judge_result.assert_evidence,
        autorater_passed=judge_result.autorater_passed,
        autorater_reasoning=judge_result.autorater_reasoning,
    )


def _no_model_call_observed(result: AttemptResult) -> bool:
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
class _PreJudgeExit:
    """An attempt that ends before the judge: its label and why.

    The reason travels with the label so the evidence a record carries is the
    branch that fired, never a second guess at it.
    """

    outcome: Outcome
    evidence: str


def _classify_pre_judge(harness: AttemptResult) -> _PreJudgeExit | None:
    """The terminal outcome an attempt earns from its harness result alone.

    Returns a ``TIMEOUT`` or ``INFRA_ERROR`` exit when the attempt never got a
    fair shot, so it must skip cheat detection and the (paid) judge entirely;
    returns ``None`` when the attempt ran cleanly enough to proceed.
    ``assemble_attempt`` both skips on it and records it as the label, so the
    two cannot disagree. The harness's own error, when it reported one, is the
    evidence; otherwise the branch says what it saw.
    """

    def ends(outcome: Outcome, seen: str) -> _PreJudgeExit:
        return _PreJudgeExit(outcome, harness.error or seen)

    exited = f"harness exited {harness.exit_code}"
    if harness.timed_out:
        return ends(Outcome.TIMEOUT, exited)

    if harness.exit_code != 0:
        return ends(Outcome.INFRA_ERROR, exited)

    # Zero exit, but the CLI said the provider refused: a throttle that
    # outlasted its retries. Read from what the CLI wrote, so an attempt that
    # passed while writing about rate limits is not one (docs/adr/0030).
    # Before the no-model-call check, whose evidence is only a guess at why.
    if harness.refusal is not None:
        return _PreJudgeExit(Outcome.INFRA_ERROR, harness.refusal.message)

    # Zero exit, but no model call was ever seen: there is no attempt to judge,
    # and an activation check would read "nothing fired" as a verdict on the
    # skill rather than on a CLI that never started (docs/adr/0001).
    if _no_model_call_observed(harness):
        return ends(
            Outcome.INFRA_ERROR,
            "no model call observed: nothing parsed from the agent's stream "
            "and no tokens reported",
        )

    return None


def _judge_outcome(judge: JudgeResult) -> Outcome:
    """The outcome an attempt earns from the verdict the judge returned.

    ``JUDGE_ERROR`` when the judge produced no usable verdict; otherwise the
    verdict itself. The last step of the precedence ``assemble_attempt`` walks,
    reached only by an attempt that got a fair shot and had a check to grade.
    """
    if judge.errored:
        return Outcome.JUDGE_ERROR
    return Outcome.PASS if judge.passed else Outcome.TASK_FAIL


def _persist_transcript(turns: list[ConversationTurn]) -> list[TranscriptTurn]:
    return [TranscriptTurn(**asdict(turn)) for turn in turns]
