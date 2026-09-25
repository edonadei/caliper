from __future__ import annotations

import pytest

from caliper.activation import ActivationDetector
from caliper.attempt import assemble_attempt
from caliper.harness.base import AttemptResult, ConversationTurn
from caliper.judge.base import JudgeResult
from caliper.schema.results import Outcome, TokenUsage
from caliper.schema.spec import TaskSpec


# --- doubles ---------------------------------------------------------------


class RecordingJudge:
    """A judge that answers with a fixed verdict and remembers being called."""

    backend = "test"
    model = None

    def __init__(self, result: JudgeResult | None = None) -> None:
        self.result = result or JudgeResult(passed=True, reasoning="looks right")
        self.calls = 0

    def evaluate(
        self,
        task: TaskSpec,
        transcript: list[ConversationTurn],
        final_output: str,
        spec_dir: str,
        workdir: str,
    ) -> JudgeResult:
        self.calls += 1
        return self.result


class StubSandbox:
    """Reports a fixed violation list, whatever the transcript says."""

    def __init__(self, found: list[str] | None = None) -> None:
        self.found = found or []

    def violations(self, transcript: list[ConversationTurn]) -> list[str]:
        return list(self.found)


def _task(**overrides) -> TaskSpec:
    fields = {"id": "task-001", "name": "t", "prompt": "go", "expect": "it works"}
    fields.update(overrides)
    return TaskSpec(**fields)


def _read_turn(path: str) -> ConversationTurn:
    return ConversationTurn(
        role="tool_use",
        content="[tool: Read]",
        tool_name="Read",
        tool_input={"file_path": path},
    )


def _result(**overrides) -> AttemptResult:
    fields = dict(
        transcript=[ConversationTurn(role="assistant", content="done")],
        final_output="done",
        exit_code=0,
        duration_seconds=1.5,
    )
    fields.update(overrides)
    return AttemptResult(**fields)


def _assemble(result: AttemptResult, **overrides):
    kwargs = dict(
        attempt=1,
        task=_task(),
        spec_dir="/tmp",
        workdir="/tmp",
        expected_activation=None,
        activation=ActivationDetector([], frozenset()),
        sandbox=StubSandbox(),
        judge=RecordingJudge(),
    )
    kwargs.update(overrides)
    return assemble_attempt(result, **kwargs)


# --- the judged path -------------------------------------------------------


def test_a_clean_attempt_with_a_passing_verdict_is_a_pass():
    judge = RecordingJudge(
        JudgeResult(
            passed=True,
            reasoning="ok",
            assert_passed=True,
            assert_evidence="",
            autorater_passed=True,
            autorater_reasoning="ok",
        )
    )

    assembled = _assemble(_result(), judge=judge)

    assert assembled.record.outcome is Outcome.PASS
    assert assembled.record.autorater_passed is True
    assert assembled.record.assert_passed is True
    assert judge.calls == 1


def test_a_failing_verdict_is_a_task_fail():
    judge = RecordingJudge(JudgeResult(passed=False, reasoning="nope"))

    assembled = _assemble(_result(), judge=judge)

    assert assembled.record.outcome is Outcome.TASK_FAIL


def test_the_attempt_record_carries_the_harness_result_verbatim():
    usage = TokenUsage(input_tokens=10, output_tokens=4)
    transcript = [ConversationTurn(role="assistant", content="hi")]

    assembled = _assemble(
        _result(
            duration_seconds=2.25,
            final_output="the answer",
            usage=usage,
            transcript=transcript,
        )
    )

    record = assembled.record
    assert record.duration_seconds == 2.25
    assert record.output == "the answer"
    assert record.usage == usage
    assert [t.content for t in record.transcript or []] == ["hi"]


def test_the_judges_resolved_model_is_reported_back():
    judge = RecordingJudge(
        JudgeResult(passed=True, reasoning="ok", resolved_model="claude-sonnet-5")
    )

    assembled = _assemble(_result(), judge=judge)

    assert assembled.judge_model == "claude-sonnet-5"


def test_no_judge_model_when_the_autorater_reports_none():
    assert _assemble(_result()).judge_model is None


# --- the early exits, in precedence order ----------------------------------


def test_a_timeout_never_reaches_the_judge():
    judge = RecordingJudge()

    assembled = _assemble(
        _result(timed_out=True, exit_code=124, error="timeout"), judge=judge
    )

    assert assembled.record.outcome is Outcome.TIMEOUT
    assert assembled.record.assert_evidence == "timeout"
    assert judge.calls == 0


def test_a_nonzero_exit_is_infra_error_with_the_exit_code_as_evidence():
    judge = RecordingJudge()

    assembled = _assemble(_result(exit_code=1), judge=judge)

    assert assembled.record.outcome is Outcome.INFRA_ERROR
    assert assembled.record.assert_evidence == "harness exited 1"
    assert judge.calls == 0


def test_no_observed_model_call_is_infra_error_and_skips_the_judge():
    judge = RecordingJudge()

    assembled = _assemble(
        _result(transcript=[], final_output="", usage=TokenUsage(input_tokens=0)),
        judge=judge,
    )

    assert assembled.record.outcome is Outcome.INFRA_ERROR
    assert assembled.record.assert_evidence == (
        "no model call observed: nothing parsed from the agent's stream "
        "and no tokens reported"
    )
    assert judge.calls == 0


def test_a_forbidden_file_read_is_a_cheat_and_skips_the_judge():
    judge = RecordingJudge()

    assembled = _assemble(
        _result(), sandbox=StubSandbox(["/x/answers.txt"]), judge=judge
    )

    assert assembled.record.outcome is Outcome.CHEAT
    assert assembled.record.cheat_evidence == ["/x/answers.txt"]
    assert judge.calls == 0


def test_a_task_with_no_execution_check_is_not_checked_and_skips_the_judge():
    judge = RecordingJudge()

    assembled = _assemble(
        _result(), task=_task(expect=None, activates=["tdd"]), judge=judge
    )

    assert assembled.record.outcome is Outcome.NOT_CHECKED
    assert judge.calls == 0


def test_a_cheat_outranks_a_missing_execution_check():
    """A trigger probe that read the answer key is still a cheat."""
    assembled = _assemble(
        _result(),
        task=_task(expect=None, activates=["tdd"]),
        sandbox=StubSandbox(["/x/answers.txt"]),
    )

    assert assembled.record.outcome is Outcome.CHEAT


def test_a_timeout_outranks_a_missing_execution_check():
    """A trigger probe that never got a fair shot is noise, not not_checked."""
    judge = RecordingJudge()
    assembled = _assemble(
        _result(exit_code=124, timed_out=True),
        task=_task(expect=None, activates=["x"]),
        judge=judge,
    )
    assert assembled.record.outcome is Outcome.TIMEOUT
    assert judge.calls == 0


def test_an_errored_judge_is_a_judge_error_not_a_task_fail():
    """A check existed and the grader broke: unusable, not a real failure."""
    judge = RecordingJudge(JudgeResult(passed=False, reasoning="flaked", errored=True))
    assembled = _assemble(_result(), judge=judge)
    assert assembled.record.outcome is Outcome.JUDGE_ERROR


# --- activation rides on every path ----------------------------------------


def _detector() -> ActivationDetector:
    return ActivationDetector(["tdd", "grilling"], frozenset({"Skill"}))


def test_activation_is_observed_and_scored_on_a_judged_attempt():
    assembled = _assemble(
        _result(transcript=[_read_turn("/skills/tdd/SKILL.md")]),
        activation=_detector(),
        expected_activation=["tdd"],
    )

    assert assembled.record.activated == ["tdd"]
    assert assembled.record.activation_passed is True


def test_an_unexpected_activation_fails_the_exact_set_match():
    assembled = _assemble(
        _result(transcript=[_read_turn("/skills/grilling/SKILL.md")]),
        activation=_detector(),
        expected_activation=["tdd"],
    )

    assert assembled.record.activated == ["grilling"]
    assert assembled.record.activation_passed is False


def test_activation_is_observed_but_not_scored_when_nothing_was_expected():
    """An ablated run drops the expectation and keeps the observation."""
    assembled = _assemble(
        _result(transcript=[_read_turn("/skills/tdd/SKILL.md")]),
        activation=_detector(),
        expected_activation=None,
    )

    assert assembled.record.activated == ["tdd"]
    assert assembled.record.activation_passed is None


def test_activation_rides_on_a_cheat_too():
    assembled = _assemble(
        _result(transcript=[_read_turn("/skills/tdd/SKILL.md")]),
        activation=_detector(),
        expected_activation=["tdd"],
        sandbox=StubSandbox(["/x/answers.txt"]),
    )

    assert assembled.record.outcome is Outcome.CHEAT
    assert assembled.record.activated == ["tdd"]
    assert assembled.record.activation_passed is True


def test_activation_rides_on_a_trigger_probe():
    assembled = _assemble(
        _result(transcript=[_read_turn("/skills/tdd/SKILL.md")]),
        task=_task(expect=None, activates=["tdd"]),
        activation=_detector(),
        expected_activation=["tdd"],
    )

    assert assembled.record.outcome is Outcome.NOT_CHECKED
    assert assembled.record.activation_passed is True


def test_a_trigger_probe_expecting_nothing_does_not_pass_when_the_agent_never_ran():
    """`activates: []` is trivially met by an agent that never started (#132)."""
    assembled = _assemble(
        _result(transcript=[], final_output=""),
        task=_task(expect=None, activates=[]),
        activation=_detector(),
        expected_activation=[],
    )

    assert assembled.record.outcome is Outcome.INFRA_ERROR
    assert assembled.record.activation_passed is None


def test_a_truncated_transcript_yields_no_observation_rather_than_an_empty_set():
    """A timeout may have cut the transcript short: `None`, never a fabricated `[]`."""
    assembled = _assemble(
        _result(timed_out=True, exit_code=124, error="timeout", transcript=[]),
        activation=_detector(),
        expected_activation=["tdd"],
    )

    assert assembled.record.outcome is Outcome.TIMEOUT
    assert assembled.record.activated is None
    assert assembled.record.activation_passed is None


def test_a_truncated_transcript_keeps_an_activation_it_did_see():
    """Truncation hides evidence, never invents it: a positive survives as evidence."""
    assembled = _assemble(
        _result(
            timed_out=True,
            exit_code=124,
            error="timeout",
            transcript=[_read_turn("/skills/tdd/SKILL.md")],
        ),
        activation=_detector(),
        expected_activation=["tdd"],
    )

    assert assembled.record.outcome is Outcome.TIMEOUT
    assert assembled.record.activated == ["tdd"]


def test_a_kept_observation_is_evidence_and_never_a_verdict():
    """Inadmissible by outcome: the observation rides, the verdict is withheld."""
    assembled = _assemble(
        _result(
            timed_out=True,
            exit_code=124,
            error="timeout",
            transcript=[_read_turn("/skills/tdd/SKILL.md")],
        ),
        activation=_detector(),
        expected_activation=["tdd"],
    )

    assert assembled.record.activation_passed is None
    assert assembled.record.activation_scored is False
    assert assembled.record.activation_observed is False


def test_user_skill_activation_counts_as_an_unexpected_activation():
    assembled = _assemble(
        _result(
            transcript=[_read_turn("/home/.codex/skills/personal/SKILL.md")],
            user_skill_names=["personal"],
        ),
        activation=_detector(),
        expected_activation=[],
    )
    assert assembled.record.activated == ["personal"]
    assert assembled.record.activation_passed is False


def test_plugin_skill_file_reads_are_observed_under_the_namespaced_name():
    assembled = _assemble(
        _result(
            transcript=[
                _read_turn("/isolated/.claude/plugins/cache/0/skills/review/SKILL.md")
            ],
            user_skill_names=["review:review"],
            user_skill_paths={
                "review:review": "/isolated/.claude/plugins/cache/0/skills/review/SKILL.md"
            },
        ),
        activation=_detector(),
        expected_activation=[],
    )
    assert assembled.record.activated == ["review:review"]
    assert assembled.record.activation_passed is False


def test_plugin_read_does_not_credit_a_declared_skill_with_the_same_basename():
    path = "/isolated/.claude/plugins/cache/0/skills/review/SKILL.md"
    assembled = _assemble(
        _result(
            transcript=[_read_turn(path)],
            user_skill_names=["plugin:review"],
            user_skill_paths={"plugin:review": path},
        ),
        activation=ActivationDetector(["review"], frozenset({"Skill"})),
        expected_activation=["review"],
    )
    assert assembled.record.activated == ["plugin:review"]
    assert assembled.record.activation_passed is False


@pytest.mark.parametrize("declared", [[], ["review"]])
def test_relative_plugin_reads_are_observed_under_the_namespaced_name(declared):
    assembled = _assemble(
        _result(
            transcript=[
                _read_turn(
                    "../iso/.claude/plugins/cache/0/review/skills/review/SKILL.md"
                )
            ],
            user_skill_names=["review:review"],
            user_skill_paths={
                "review:review": "plugins/cache/0/review/skills/review/SKILL.md"
            },
        ),
        activation=ActivationDetector(declared, frozenset({"Skill"})),
        expected_activation=[],
    )
    assert assembled.record.activated == ["review:review"]
