from __future__ import annotations

import json
import subprocess

from caliper.harness.base import ConversationTurn
from caliper.judge.script_assert import EvalJudge
from caliper.schema.spec import TaskSpec


def test_eval_judge_claude_code_invokes_claude_cli(monkeypatch, tmp_path) -> None:
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return subprocess.CompletedProcess(
            cmd,
            0,
            stdout='{"mode": "verdict", "passed": true, "reasoning": "The transcript says hello."}',
            stderr="",
        )

    monkeypatch.setattr("caliper.judge.claude_code_judge.subprocess.run", fake_run)

    result = EvalJudge(backend="claude", model="claude-test").evaluate(
        task=TaskSpec(
            id="task-001",
            name="Claude judge",
            prompt="Say hello",
            expect="The assistant says hello.",
        ),
        transcript=[ConversationTurn(role="assistant", content="hello")],
        final_output="hello",
        spec_dir=str(tmp_path),
    )

    assert result.passed is True
    assert result.autorater_passed is True
    assert result.autorater_reasoning == "The transcript says hello."

    cmd, kwargs = calls[0]
    assert cmd[:2] == ["claude", "-p"]
    assert "--output-format" in cmd
    assert cmd[cmd.index("--model") + 1] == "claude-test"
    assert "The assistant says hello." in cmd[2]
    assert kwargs["timeout"] == 60


def test_claude_judge_extracts_concrete_model_from_envelope(
    monkeypatch, tmp_path
) -> None:
    """The verdict lives in `.result`; the concrete model in `.modelUsage`."""
    envelope = {
        "type": "result",
        "result": '{"mode": "verdict", "passed": true, "reasoning": "ok"}',
        "modelUsage": {"claude-opus-4-8": {"inputTokens": 10, "outputTokens": 2}},
    }

    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(
            cmd, 0, stdout=json.dumps(envelope), stderr=""
        )

    monkeypatch.setattr("caliper.judge.claude_code_judge.subprocess.run", fake_run)

    # No model requested → the judge should still record what Claude actually used.
    result = EvalJudge(backend="claude-code").evaluate(
        task=TaskSpec(id="t1", name="t", prompt="p", expect="says ok"),
        transcript=[ConversationTurn(role="assistant", content="ok")],
        final_output="ok",
        spec_dir=str(tmp_path),
    )

    assert result.passed is True
    assert result.autorater_passed is True
    assert result.resolved_model == "claude-opus-4-8"
