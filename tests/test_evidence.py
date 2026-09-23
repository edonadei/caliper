"""Evidence views, mixed-view requests, batching, and oversized evidence."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from caliper.harness.base import ConversationTurn
from caliper.judge import EvalJudge, evidence
from caliper.judge.jev import (
    API_KEY_ENV,
    STATE_TOKEN_LIMIT,
    ChoiceAnswer,
    ChoiceQuestion,
    JevError,
    JevFailure,
    JevResponse,
    ask_choices,
)
from caliper.schema.results import ClassificationVerdict
from caliper.schema.spec import EVIDENCE_VIEWS, ClassifyCheck, TaskSpec

TRANSCRIPT = [
    ConversationTurn(role="user", content="Where are A-1 and B-2?"),
    ConversationTurn(role="assistant", content="Checking both."),
    ConversationTurn(
        role="tool_use", content="", tool_name="lookup", tool_input={"id": "A-1"}
    ),
    ConversationTurn(
        role="tool_use", content="", tool_name="lookup", tool_input={"id": "B-2"}
    ),
    ConversationTurn(role="tool_result", content="A-1: Thursday", tool_output=None),
    ConversationTurn(role="tool_result", content="ignored", tool_output="B-2: Friday"),
    ConversationTurn(role="tool_use", content="", tool_name="ping", tool_input=None),
    ConversationTurn(role="assistant", content="A-1 Thursday, B-2 Friday."),
]


def project(view: str) -> dict:
    return evidence.VIEWS[view]("Where are A-1 and B-2?", TRANSCRIPT, "A-1 Thu.")


# --- views -----------------------------------------------------------------


def test_the_schema_enum_and_the_projections_are_the_same_set():
    assert set(EVIDENCE_VIEWS) == set(evidence.VIEWS)


def test_output_view_is_prompt_and_answer_only():
    assert project("output") == {
        "task_prompt": "Where are A-1 and B-2?",
        "final_output": "A-1 Thu.",
    }


def test_tool_trace_pairs_each_result_with_its_call_in_order():
    assert project("tool_trace") == {
        "task_prompt": "Where are A-1 and B-2?",
        "tool_calls": [
            {"tool": "lookup", "input": {"id": "A-1"}, "result": "A-1: Thursday"},
            {"tool": "lookup", "input": {"id": "B-2"}, "result": "B-2: Friday"},
            {"tool": "ping", "input": {}, "result": None},
        ],
        "final_output": "A-1 Thu.",
    }


def test_tool_trace_keeps_a_result_with_no_call_rather_than_guessing():
    orphan = [ConversationTurn(role="tool_result", content="stray")]
    state = evidence.tool_trace("p", orphan, "o")
    assert state["tool_calls"] == [{"tool": None, "input": None, "result": "stray"}]


def test_full_trace_keeps_every_event_in_order_with_call_links():
    events = project("full_trace")["events"]
    assert [e["type"] for e in events] == [
        "message",
        "message",
        "tool_call",
        "tool_call",
        "tool_result",
        "tool_result",
        "tool_call",
        "message",
    ]
    assert events[0] == {
        "type": "message",
        "role": "user",
        "content": TRANSCRIPT[0].content,
    }
    assert (events[2]["call_id"], events[4]["call_id"]) == ("call-1", "call-1")
    assert (events[3]["call_id"], events[5]["call_id"]) == ("call-2", "call-2")
    assert events[5]["output"] == "B-2: Friday"


@pytest.mark.parametrize("view", EVIDENCE_VIEWS)
def test_every_view_is_deterministic_and_json_serializable(view):
    assert json.dumps(project(view)) == json.dumps(project(view))
    assert project(view)["task_prompt"] and "final_output" in project(view)


def test_evidence_is_required_and_closed():
    fields = {
        "name": "c",
        "question": "q?",
        "choices": {"a": None, "b": None, "u": None},
        "require": "a",
        "abstain": "u",
        "min_probability": 0.8,
    }
    with pytest.raises(ValidationError, match="evidence"):
        ClassifyCheck(**fields)
    with pytest.raises(ValidationError, match="must be one of output, tool_trace"):
        ClassifyCheck(**fields, evidence="everything")
    for view in ("output", "tool_trace", "full_trace"):
        assert ClassifyCheck(**fields, evidence=view).evidence == view


# --- batching and mixed views ----------------------------------------------


def check(name: str, view: str) -> dict:
    return {
        "name": name,
        "evidence": view,
        "question": f"{name}?",
        "choices": {"yes": None, "no": None, "unclear": None},
        "require": "yes",
        "abstain": "unclear",
        "min_probability": 0.8,
    }


class FakeJev:
    def __init__(self, answers: dict[str, str], error_for: set[str] = frozenset()):
        self.answers = answers
        self.error_for = error_for
        self.calls: list[tuple[dict, list[str]]] = []

    def __call__(self, state, questions):
        self.calls.append((state, list(questions)))
        if self.error_for & set(questions):
            raise JevError(JevFailure.PROVIDER, "HTTP 529")
        answers = {}
        for qid, q in questions.items():
            choice = self.answers[qid]
            answers[qid] = ChoiceAnswer(
                choice=choice,
                probabilities={c: 0.9 if c == choice else 0.05 for c in q.criteria},
            )
        return JevResponse(model="jev-1.13.0", answers=answers, latency_seconds=0.1)


def grade(checks: list[dict], fake: FakeJev, tmp_path: Path):
    task = TaskSpec(name="t", prompt="Where are A-1 and B-2?", classify=checks)
    return EvalJudge(ask=fake).evaluate(task, TRANSCRIPT, "A-1 Thu.", str(tmp_path))


def test_one_request_per_view_and_names_map_back(tmp_path):
    checks = [
        check("a", "tool_trace"),
        check("b", "output"),
        check("c", "tool_trace"),
        check("d", "full_trace"),
    ]
    fake = FakeJev({"a": "yes", "b": "yes", "c": "yes", "d": "yes"})
    result = grade(checks, fake, tmp_path)
    assert [qids for _, qids in fake.calls] == [["a", "c"], ["b"], ["d"]]
    assert [set(state) for state, _ in fake.calls] == [
        {"task_prompt", "tool_calls", "final_output"},
        {"task_prompt", "final_output"},
        {"task_prompt", "events", "final_output"},
    ]
    assert [c.name for c in result.classifications] == ["a", "b", "c", "d"]
    assert [c.evidence for c in result.classifications] == [
        "tool_trace",
        "output",
        "tool_trace",
        "full_trace",
    ]
    assert result.passed


def test_mixed_views_compose_a_failure_over_an_error(tmp_path):
    checks = [check("a", "tool_trace"), check("b", "output")]
    fake = FakeJev({"a": "no", "b": "yes"}, error_for={"b"})
    result = grade(checks, fake, tmp_path)
    verdicts = [c.verdict for c in result.classifications]
    assert verdicts == [ClassificationVerdict.FAIL, ClassificationVerdict.ERROR]
    assert (result.passed, result.errored) == (False, False)


def test_mixed_views_compose_an_error_over_a_pass(tmp_path):
    checks = [check("a", "tool_trace"), check("b", "output")]
    result = grade(checks, FakeJev({"a": "yes", "b": "unclear"}), tmp_path)
    assert (result.passed, result.errored) == (False, True)


# --- oversized evidence ----------------------------------------------------


QUESTION = {"q": ChoiceQuestion(instructions="q?", criteria={"a": None, "b": None})}


def refuse_to_send(*_):
    raise AssertionError("oversized evidence must not be sent")


def test_oversized_state_is_refused_before_sending_with_its_size():
    state = {"final_output": "x" * (STATE_TOKEN_LIMIT * 3 + 10)}
    with pytest.raises(JevError) as err:
        ask_choices(state, QUESTION, env={API_KEY_ENV: "k"}, transport=refuse_to_send)
    assert err.value.kind is JevFailure.OVERSIZE
    assert "bytes of evidence" in err.value.message
    assert "narrower evidence view" in err.value.message


def test_oversize_is_reported_even_without_a_key():
    state = "x" * (STATE_TOKEN_LIMIT * 3 + 10)
    with pytest.raises(JevError) as err:
        ask_choices(state, QUESTION, env={}, transport=refuse_to_send)
    assert err.value.kind is JevFailure.OVERSIZE


def test_many_questions_over_the_request_budget_are_refused():
    big = "y" * 30_000
    questions = {
        f"q{i}": ChoiceQuestion(instructions=big, criteria={"a": None, "b": None})
        for i in range(8)
    }
    with pytest.raises(JevError) as err:
        ask_choices("s", questions, env={API_KEY_ENV: "k"}, transport=refuse_to_send)
    assert err.value.kind is JevFailure.OVERSIZE
    assert "request limit" in err.value.message


def test_a_provider_context_rejection_gets_the_same_guidance():
    def reject(*_):
        return 422, b'{"detail": "context length exceeded"}'

    with pytest.raises(JevError) as err:
        ask_choices("s", QUESTION, env={API_KEY_ENV: "k"}, transport=reject)
    assert err.value.kind is JevFailure.REJECTED
    assert "narrower evidence view" in err.value.message


def test_oversized_evidence_is_a_judge_error_and_nothing_is_truncated(
    tmp_path, monkeypatch
):
    monkeypatch.setenv(API_KEY_ENV, "k")
    huge = "z" * (STATE_TOKEN_LIMIT * 3 + 10)
    sent: list = []

    def ask(state, questions):
        return ask_choices(
            state, questions, transport=lambda *a: sent.append(a) or (500, b"")
        )

    task = TaskSpec(name="t", prompt="p", classify=[check("a", "output")])
    result = EvalJudge(ask=ask).evaluate(task, [], huge, str(tmp_path))
    (record,) = result.classifications
    assert sent == []
    assert result.errored
    assert record.verdict == ClassificationVerdict.ERROR
    assert record.error.startswith("oversize: evidence too large")
