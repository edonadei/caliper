"""The Jev adoption benchmark's corpus, decision rules and gate.

The benchmark script lives outside the package (it is a product experiment, not
a shipped feature), so it is loaded by path. Every Jev call is answered by a
fake transport: no network, no key.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from caliper.judge.jev import API_KEY_ENV, JEV_MODEL

SCRIPT = Path(__file__).parent.parent / "benchmarks" / "tool-grounding" / "run.py"


@pytest.fixture(scope="module")
def bench():
    spec = importlib.util.spec_from_file_location("tool_grounding_bench", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    # Dataclasses resolve their module through sys.modules.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    yield module
    sys.modules.pop(spec.name, None)


def fake_jev(labels: dict[str, str], prob: float = 0.9, model: str = JEV_MODEL):
    """A transport that answers each trace (keyed by task prompt) with a label."""

    def send(url, headers, payload, timeout):
        body = json.loads(payload)
        ((qid, question),) = body["questions"].items()
        choice = labels[body["state"]["task_prompt"]]
        rest = (1 - prob) / (len(question["criteria"]) - 1)
        probs = {c: (prob if c == choice else rest) for c in question["criteria"]}
        return 200, json.dumps(
            {
                "model": model,
                "answers": {
                    qid: {"type": "choice", "choice": choice, "probabilities": probs}
                },
            }
        ).encode()

    return send


def human_labels(bench) -> dict[str, str]:
    return {t.task_prompt: t.label for t in bench.load_corpus()}


def test_corpus_is_the_five_approved_cases(bench):
    corpus = bench.load_corpus()
    classifier = bench.load_classifier()
    assert [t.id for t in corpus] == [
        "01-grounded-paraphrase",
        "02-subtle-contradiction",
        "03-unused-tool-result",
        "04-insufficient-evidence",
        "05-prompt-injected-evidence",
    ]
    for trace in corpus:
        assert trace.label in classifier.choices
        roles = [t.role for t in trace.transcript]
        assert "tool_use" in roles and "tool_result" in roles
    assert [t.label for t in corpus].count(classifier.require) == 1
    assert [bench.expected_decision(classifier, t.label) for t in corpus] == [
        "pass",
        "fail",
        "fail",
        "judge_error",
        "fail",
    ]


def test_the_corpus_classifier_is_a_valid_spec_check(bench):
    check = bench.load_classifier()
    assert (check.require, check.abstain, check.min_probability) == (
        "grounded",
        "unclear",
        0.8,
    )


@pytest.mark.parametrize(
    "choice, prob, decision",
    [
        ("grounded", 0.9, "pass"),
        ("contradicted", 0.9, "fail"),
        ("unclear", 0.99, "judge_error"),
        ("grounded", 0.79, "judge_error"),
    ],
)
def test_decisions_use_the_shipped_classify_rules(bench, choice, prob, decision):
    corpus = bench.load_corpus()
    labels = {t.task_prompt: choice for t in corpus}
    got = bench.grade_with_jev(
        corpus[0],
        bench.load_classifier(),
        env={API_KEY_ENV: "k"},
        transport=fake_jev(labels, prob=prob),
    )
    assert got.decision == decision
    assert got.error is None


def test_all_correct_without_cli_comparison_is_incomplete(bench):
    reports, summary = bench.run(
        repeats=3,
        cli_target=None,
        env={API_KEY_ENV: "k"},
        transport=fake_jev(human_labels(bench)),
    )
    assert summary["jev"]["correct_decisions"] == 15
    assert summary["jev"]["false_passes"] == 0
    assert all(r.repeatability() == 1.0 for r in reports)
    assert summary["gate"]["checks"]["latency_at_most_one_fifth_of_cli_judge"] is None
    assert summary["gate"]["verdict"] == "incomplete"


def test_a_false_pass_on_the_injected_trace_fails_the_gate(bench):
    labels = human_labels(bench)
    injected = next(t for t in bench.load_corpus() if "prompt-injected" in t.id)
    labels[injected.task_prompt] = "grounded"
    _, summary = bench.run(
        repeats=1,
        cli_target=None,
        env={API_KEY_ENV: "k"},
        transport=fake_jev(labels),
    )
    checks = summary["gate"]["checks"]
    assert summary["jev"]["false_passes"] == 1
    assert checks["zero_false_passes_on_prompt_injection"] is False
    assert summary["gate"]["verdict"] == "fail"


def test_an_unpinned_model_fails_the_gate(bench):
    _, summary = bench.run(
        repeats=1,
        cli_target=None,
        env={API_KEY_ENV: "k"},
        transport=fake_jev(human_labels(bench), model="jev-1.14.0"),
    )
    assert summary["gate"]["checks"]["pinned_model_answered"] is False
    assert summary["gate"]["verdict"] == "fail"


def test_missing_key_exits_with_an_actionable_error(bench, monkeypatch, capsys):
    monkeypatch.delenv(API_KEY_ENV, raising=False)
    assert bench.main(["--repeats", "1"]) == 2
    err = capsys.readouterr().err
    assert API_KEY_ENV in err


def test_report_states_the_narrow_sample_caveat(bench):
    reports, summary = bench.run(
        repeats=1,
        cli_target=None,
        env={API_KEY_ENV: "k"},
        transport=fake_jev(human_labels(bench)),
    )
    text = bench.render(reports, summary, 1)
    assert "not evidence of general judge accuracy" in text
    assert "grounded=0.90" in text


def test_gate_passes_when_accurate_and_much_faster_than_the_cli_judge(
    bench, monkeypatch
):
    def slow_correct_cli(trace, judge):
        decision = bench.expected_decision(bench.load_classifier(), trace.label)
        return bench.Decision(decision=decision, latency_seconds=8.0)

    monkeypatch.setattr(bench, "grade_with_cli", slow_correct_cli)
    reports, summary = bench.run(
        repeats=2,
        cli_target="claude-code",
        env={API_KEY_ENV: "k"},
        transport=fake_jev(human_labels(bench)),
    )
    assert all(len(r.cli) == 2 for r in reports)
    assert summary["cli"]["latency_median_seconds"] == 8.0
    assert summary["gate"]["checks"]["latency_at_most_one_fifth_of_cli_judge"]
    assert summary["gate"]["verdict"] == "pass"
