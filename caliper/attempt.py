"""Assembling one attempt's record from a finished harness run.

This is **the seam where an attempt is assembled** (docs/CONTEXT.md → Outcome):
given an ``AttemptResult`` the harness already produced, decide what happened
and return the ``AttemptRecord`` that goes into the results file. Every grading
rule lives here — the pre-judge skip, activation, cheat detection, the (paid)
judge call, and the precedence between them.

Deliberately **pure over an already-produced result**: no threads, no temp
directories, no subprocesses. Those belong to the runner, which owns an
attempt's *lifecycle* while this module owns its *verdict*. That split is what
lets the rules most likely to change be tested without standing up a fake
harness and a thread pool.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Protocol

from caliper.activation import ActivationDetector, check_activation
from caliper.harness.base import AttemptResult, ConversationTurn
from caliper.judge.base import Judge
from caliper.outcome import classify_outcome, classify_pre_judge
from caliper.schema.results import AttemptRecord, Outcome, TranscriptTurn
from caliper.schema.spec import TaskSpec


class CheatDetector(Protocol):
    """What this module needs of a cheat detector: violations from a transcript.

    A structural seam, like :class:`caliper.judge.base.Judge` — the production
    detector lives with the runner that configures it from
    ``sandbox.forbidden_files``, and a test double conforms by shape.
    """

    def check(self, transcript: list[ConversationTurn]) -> list[str]: ...


@dataclass(frozen=True)
class AssembledAttempt:
    """One attempt's record, plus what the assembly observed about the run.

    ``judge_model`` is the concrete model the autorater resolved, when one ran
    and reported it. It rides out here rather than on the record because it is a
    *run*-level fact (it lands in ``RunMeta``) — returned rather than written
    into the run's collector, so this module stays pure and the runner keeps
    ownership of what it accumulates across attempts.
    """

    record: AttemptRecord
    judge_model: str | None = None


def assemble_attempt(
    result: AttemptResult,
    *,
    attempt: int,
    task: TaskSpec,
    spec_dir: str,
    expected_activation: list[str] | None,
    activation: ActivationDetector,
    cheat: CheatDetector,
    judge: Judge,
) -> AssembledAttempt:
    """Grade one finished harness run into an ``AttemptRecord``.

    Precedence follows ``classify_outcome``: timeout / infra error, then cheat,
    then a missing execution check, then the judge's verdict. Each of the first
    three exits *before* the judge is called, so an attempt that never got a
    fair shot never spends a paid autorater call on garbage output.

    ``expected_activation`` is what this run asserts the task should activate —
    ``None`` on an ablated run, which drops the expectation but keeps the
    observation (docs/adr/0015-ablation-names-its-subject-at-the-invocation.md).

    ``attempt`` is the caller's own counter rather than ``result.attempt``: the
    runner decides which of the k attempts this is, and the harness only echoes
    it back, so the record is numbered from the authority instead of the echo.
    """
    # A timeout or infrastructure signal terminates the attempt before we spend
    # a (paid) judge call on garbage output. The pre-judge classifier owns that
    # predicate — nothing re-derives it — so the skip here and the final label
    # can never drift apart.
    pre_judge_outcome = classify_pre_judge(result)

    # Recorded on *every* attempt that produced a whole transcript, asserted only
    # when the task said so. A timeout/infra failure yields ``None`` — *not
    # observed*, never a fabricated empty set — because the transcript may have
    # been truncated, where an empty result would be a confident "the description
    # never fired" manufactured from nothing. (Ablating the whole neighbourhood
    # yields ``None`` too, from the detector itself: nothing installed means no
    # choice existed to observe.)
    activated = (
        activation.detect(result.transcript) if pre_judge_outcome is None else None
    )
    activation_passed = check_activation(activated, expected_activation)

    def with_outcome(
        outcome: Outcome, judge_model: str | None = None, **verdict
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
                **verdict,
            ),
            judge_model=judge_model,
        )

    if pre_judge_outcome is not None:
        evidence = result.error or f"harness exited {result.exit_code}"
        return with_outcome(pre_judge_outcome, assert_evidence=evidence)

    cheat_violations = cheat.check(result.transcript)
    if cheat_violations:
        return with_outcome(
            classify_outcome(result, cheat_violations, None),
            cheat_evidence=cheat_violations,
        )

    # An `activates:`-only task authored no execution check, so there is nothing
    # to grade — skip the (paid) judge call rather than spending it to receive a
    # non-verdict and label the attempt an error.
    if not (task.expect or task.assert_script):
        return with_outcome(
            classify_outcome(result, [], None, has_execution_check=False)
        )

    judge_result = judge.evaluate(
        task=task,
        transcript=result.transcript,
        final_output=result.final_output,
        spec_dir=spec_dir,
    )
    return with_outcome(
        classify_outcome(result, [], judge_result),
        judge_model=judge_result.resolved_model,
        assert_passed=judge_result.assert_passed,
        assert_evidence=judge_result.assert_evidence,
        autorater_passed=judge_result.autorater_passed,
        autorater_reasoning=judge_result.autorater_reasoning,
    )


def _persist_transcript(turns: list[ConversationTurn]) -> list[TranscriptTurn]:
    return [TranscriptTurn(**asdict(turn)) for turn in turns]
