"""``classify:`` checks: schema, Jev decision rules, composition, saved records.

Jev is always a fake ``ask``; nothing here opens a socket or needs a key.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from caliper.activation import ActivationDetector
from caliper.attempt import assemble_attempt
from caliper.harness.base import AttemptResult, ConversationTurn
from caliper.judge import EvalJudge
from caliper.judge.jev import (
    API_KEY_ENV,
    ChoiceAnswer,
    JevError,
    JevFailure,
    JevResponse,
    ask_choices,
)
from caliper.schema.results import ClassificationVerdict, Outcome
from caliper.schema.spec import ClassifyCheck, TaskSpec, load_spec

CHOICES = {
    "grounded": "Follows from the tool result.",
    "contradicted": "Conflicts with the tool result.",
    "cannot_tell": None,
}


def check(**overrides) -> dict:
    fields = {
        "name": "grounded_in_tool_result",
        "evidence": "tool_trace",
        "question": "Is the final output grounded in the tool results?",
        "choices": dict(CHOICES),
        "require": "grounded",
        "abstain": "cannot_tell",
        "min_probability": 0.7,
    }
    fields.update(overrides)
    return fields


def task(*checks: dict, **overrides) -> TaskSpec:
    fields = {"name": "t", "prompt": "Where is order A-1?", "classify": list(checks)}
    fields.update(overrides)
    return TaskSpec(**fields)


TRANSCRIPT = [
    ConversationTurn(
        role="tool_use",
        content="",
        tool_name="mcp__orders__lookup_order",
        tool_input={"order_id": "A-1"},
    ),
    ConversationTurn(
        role="assistant", content="Let me check that for you."
    ),  # dropped by tool_trace
    ConversationTurn(
        role="tool_result",
        content='{"eta": "Thursday"}',
        tool_output='{"eta": "Thursday"}',
    ),
    ConversationTurn(role="assistant", content="It arrives Thursday."),
]


class FakeJev:
    """Answers every question with a fixed choice/probability; records calls."""

    def __init__(self, answers: dict[str, tuple[str, float]] | None = None, error=None):
        self.answers = answers or {}
        self.error = error
        self.calls: list[tuple[object, dict]] = []

    def __call__(self, state, questions):
        self.calls.append((state, questions))
        if self.error:
            raise self.error
        out = {}
        for qid, question in questions.items():
            choice, prob = self.answers.get(qid, ("grounded", 0.9))
            rest = (1 - prob) / (len(question.criteria) - 1)
            out[qid] = ChoiceAnswer(
                choice=choice,
                probabilities={
                    c: (prob if c == choice else rest) for c in question.criteria
                },
            )
        return JevResponse(
            model="jev-1.13.0", answers=out, latency_seconds=0.12, request_bytes=512
        )


def judge_with(fake: FakeJev) -> EvalJudge:
    return EvalJudge(ask=fake)


def evaluate(t: TaskSpec, fake: FakeJev, tmp_path: Path):
    return judge_with(fake).evaluate(
        t, TRANSCRIPT, "It arrives Thursday.", str(tmp_path)
    )


# --- schema ----------------------------------------------------------------


def test_a_classify_only_task_is_a_valid_task():
    t = task(check())
    assert t.has_execution_check
    assert t.classify[0].require == "grounded"


@pytest.mark.parametrize(
    "overrides, message",
    [
        ({"require": "perfect"}, "require 'perfect' is not one of its choices"),
        ({"abstain": "unsure"}, "abstain 'unsure' is not one of its choices"),
        ({"abstain": "grounded"}, "require and abstain must be different"),
        ({"choices": {"grounded": None, "cannot_tell": None}}, "at least three"),
        ({"min_probability": -0.1}, "min_probability must be between 0 and 1"),
        ({"min_probability": 1.5}, "min_probability must be between 0 and 1"),
        ({"evidence": "vibes"}, "invalid evidence 'vibes'"),
        ({"name": "has space"}, "invalid classify name"),
        ({"question": "  "}, "question must not be empty"),
    ],
)
def test_invalid_checks_are_rejected(overrides, message):
    with pytest.raises(ValidationError, match=message):
        ClassifyCheck(**check(**overrides))


@pytest.mark.parametrize(
    "missing", ["name", "evidence", "question", "choices", "require", "abstain"]
)
def test_every_contract_field_is_required(missing):
    fields = check()
    del fields[missing]
    with pytest.raises(ValidationError, match=missing):
        ClassifyCheck(**fields)


def test_min_probability_is_mandatory():
    fields = check()
    del fields["min_probability"]
    with pytest.raises(ValidationError, match="min_probability"):
        ClassifyCheck(**fields)


def test_unknown_keys_are_rejected():
    with pytest.raises(ValidationError, match="threshold"):
        ClassifyCheck(**check(threshold=0.5))


def test_check_names_must_be_unique_within_a_task():
    with pytest.raises(ValidationError, match="must be unique: dup"):
        task(check(name="dup"), check(name="dup"))


def test_an_empty_classify_list_is_rejected():
    with pytest.raises(ValidationError, match="at least one check"):
        task()


def test_classify_loads_from_yaml(tmp_path):
    spec = tmp_path / "g.eval.yaml"
    spec.write_text(
        """
tasks:
  - name: grounded
    prompt: Where is order A-1?
    classify:
      - name: grounded_in_tool_result
        evidence: tool_trace
        question: Is the final output grounded in the tool results?
        choices:
          grounded: Follows from the tool result.
          contradicted: Conflicts with it.
          cannot_tell:
        require: grounded
        abstain: cannot_tell
        min_probability: 0.7
"""
    )
    loaded = load_spec(spec)
    assert loaded.tasks[0].classify[0].choices["cannot_tell"] is None


# --- decision rules --------------------------------------------------------


@pytest.mark.parametrize(
    "choice, prob, verdict, passed, errored",
    [
        ("grounded", 0.9, ClassificationVerdict.PASS, True, False),
        ("contradicted", 0.9, ClassificationVerdict.FAIL, False, False),
        ("cannot_tell", 0.95, ClassificationVerdict.ERROR, False, True),
        ("grounded", 0.6, ClassificationVerdict.ERROR, False, True),
        ("contradicted", 0.6, ClassificationVerdict.ERROR, False, True),
    ],
)
def test_one_check_maps_to_an_outcome(tmp_path, choice, prob, verdict, passed, errored):
    fake = FakeJev({"grounded_in_tool_result": (choice, prob)})
    result = evaluate(task(check()), fake, tmp_path)
    (record,) = result.classifications
    assert record.verdict == verdict
    assert (result.passed, result.errored) == (passed, errored)


def test_threshold_is_inclusive(tmp_path):
    fake = FakeJev({"grounded_in_tool_result": ("grounded", 0.7)})
    assert evaluate(task(check()), fake, tmp_path).passed


def test_a_provider_failure_is_a_judge_error_with_a_typed_reason(tmp_path):
    fake = FakeJev(error=JevError(JevFailure.PROVIDER, "TypeSafe returned HTTP 529"))
    result = evaluate(task(check()), fake, tmp_path)
    (record,) = result.classifications
    assert result.errored
    assert record.verdict == ClassificationVerdict.ERROR
    assert record.error == "provider: TypeSafe returned HTTP 529"
    assert record.selected is None and record.probabilities is None


def test_a_missing_key_is_a_judge_error_that_names_the_variable(tmp_path, monkeypatch):
    monkeypatch.delenv(API_KEY_ENV, raising=False)
    result = EvalJudge(ask=ask_choices).evaluate(
        task(check()), TRANSCRIPT, "It arrives Thursday.", str(tmp_path)
    )
    assert result.errored
    assert API_KEY_ENV in result.classifications[0].error


def test_diagnostics_never_carry_the_key(tmp_path, monkeypatch):
    secret = "ts-live-abcdef"
    monkeypatch.setenv(API_KEY_ENV, secret)

    def echoing_transport(url, headers, body, timeout):
        return 500, json.dumps({"detail": f"boom {headers['Authorization']}"}).encode()

    judge = EvalJudge(ask=lambda s, q: ask_choices(s, q, transport=echoing_transport))
    result = judge.evaluate(task(check()), TRANSCRIPT, "x", str(tmp_path))
    dumped = json.dumps([c.model_dump() for c in result.classifications])
    assert secret not in dumped
    assert secret not in result.reasoning


# --- composition -----------------------------------------------------------


def two_checks() -> TaskSpec:
    return task(check(name="a"), check(name="b"))


@pytest.mark.parametrize(
    "a, b, passed, errored",
    [
        (("grounded", 0.9), ("grounded", 0.9), True, False),
        (("grounded", 0.9), ("contradicted", 0.9), False, False),
        (("contradicted", 0.9), ("cannot_tell", 0.9), False, False),
        (("grounded", 0.9), ("cannot_tell", 0.9), False, True),
        (("grounded", 0.9), ("grounded", 0.2), False, True),
    ],
)
def test_checks_compose(tmp_path, a, b, passed, errored):
    result = evaluate(two_checks(), FakeJev({"a": a, "b": b}), tmp_path)
    assert (result.passed, result.errored) == (passed, errored)


def test_a_failing_assert_fails_even_when_classify_passes(tmp_path):
    t = task(check(), **{"assert": "assert False, 'nope'"})
    result = evaluate(t, FakeJev(), tmp_path)
    assert (result.passed, result.errored) == (False, False)


def test_a_classify_error_is_not_dropped_beside_a_passing_assert(tmp_path):
    t = task(check(), **{"assert": "assert True"})
    fake = FakeJev({"grounded_in_tool_result": ("cannot_tell", 0.9)})
    result = evaluate(t, fake, tmp_path)
    assert (result.passed, result.errored) == (False, True)


def test_a_confident_classify_failure_outranks_a_classify_error(tmp_path):
    t = task(check(name="a"), check(name="b"), **{"assert": "assert True"})
    fake = FakeJev({"a": ("contradicted", 0.95), "b": ("grounded", 0.1)})
    result = evaluate(t, fake, tmp_path)
    assert (result.passed, result.errored) == (False, False)


def test_checks_on_one_view_share_one_request_and_keep_their_names(tmp_path):
    fake = FakeJev({"a": ("grounded", 0.9), "b": ("contradicted", 0.8)})
    result = evaluate(two_checks(), fake, tmp_path)
    assert len(fake.calls) == 1
    assert set(fake.calls[0][1]) == {"a", "b"}
    assert [(c.name, c.selected) for c in result.classifications] == [
        ("a", "grounded"),
        ("b", "contradicted"),
    ]


def test_tool_trace_evidence_is_structured_and_omits_assistant_prose(tmp_path):
    fake = FakeJev()
    evaluate(task(check()), fake, tmp_path)
    state, _ = fake.calls[0]
    assert state == {
        "task_prompt": "Where is order A-1?",
        "tool_calls": [
            {
                "tool": "mcp__orders__lookup_order",
                "input": {"order_id": "A-1"},
                "result": '{"eta": "Thursday"}',
            }
        ],
        "final_output": "It arrives Thursday.",
    }


# --- saved record and report -------------------------------------------------


class _Sandbox:
    def violations(self, transcript):
        return []


def _assembled(fake: FakeJev):
    result = AttemptResult(
        transcript=TRANSCRIPT,
        final_output="It arrives Thursday.",
        exit_code=0,
        duration_seconds=1.0,
    )
    return assemble_attempt(
        result,
        attempt=1,
        task=task(check()),
        spec_dir=".",
        expected_activation=None,
        activation=ActivationDetector([], frozenset()),
        sandbox=_Sandbox(),
        judge=judge_with(fake),
    ).record


def test_the_saved_record_keeps_the_whole_decision():
    record = _assembled(FakeJev({"grounded_in_tool_result": ("contradicted", 0.8)}))
    assert record.outcome == Outcome.TASK_FAIL
    saved = json.loads(record.model_dump_json())["classifications"][0]
    assert saved["name"] == "grounded_in_tool_result"
    assert saved["selected"] == "contradicted"
    assert set(saved["probabilities"]) == set(CHOICES)
    assert saved["min_probability"] == 0.7
    assert saved["latency_seconds"] == 0.12
    assert saved["model"] == "jev-1.13.0"
    assert saved["verdict"] == "fail"


def test_an_uncertain_answer_is_a_judge_error_attempt():
    record = _assembled(FakeJev({"grounded_in_tool_result": ("grounded", 0.55)}))
    assert record.outcome == Outcome.JUDGE_ERROR


def test_explanations_report_the_decision_without_inventing_reasons():
    record = _assembled(FakeJev({"grounded_in_tool_result": ("grounded", 0.55)}))
    line = record.classifications[0].explain()
    assert line.startswith("classify grounded_in_tool_result: error")
    assert "selected grounded (p=0.55" in line
    assert "below min_probability 0.70" in line


def test_an_errored_autorater_blocks_a_pass_beside_classify(tmp_path, monkeypatch):
    judge = judge_with(FakeJev())
    monkeypatch.setattr(
        judge, "_llm_evaluate", lambda *a: (False, "judge crashed", True, None)
    )
    t = task(check(), expect="it is grounded")
    result = judge.evaluate(t, TRANSCRIPT, "x", str(tmp_path))
    assert (result.passed, result.errored) == (False, True)


def test_threshold_bounds_are_inclusive():
    assert ClassifyCheck(**check(min_probability=0)).min_probability == 0
    assert ClassifyCheck(**check(min_probability=1)).min_probability == 1
