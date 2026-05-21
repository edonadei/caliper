from __future__ import annotations

import subprocess

from verdict.harness.base import ConversationTurn
from verdict.judge.claude_code_judge import ClaudeCodeJudge
from verdict.schema.spec import JudgeConfig, TaskSpec


def test_claude_code_judge_still_invokes_claude_cli(monkeypatch, tmp_path) -> None:
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return subprocess.CompletedProcess(
            cmd,
            0,
            stdout='{"passed": true, "reasoning": "The transcript says hello."}',
            stderr="",
        )

    monkeypatch.setattr("verdict.judge.claude_code_judge.subprocess.run", fake_run)

    result = ClaudeCodeJudge(JudgeConfig(backend="claude", model="claude-test")).evaluate(
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
