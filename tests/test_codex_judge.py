from __future__ import annotations

import json
import subprocess

from verdict.harness.base import ConversationTurn
from verdict.judge import ClaudeCodeJudge, CodexJudge, get_judge
from verdict.judge.codex_judge import _extract_codex_error, evaluate_with_codex
from verdict.judge.script_assert import ScriptAssertJudge
from verdict.schema.spec import JudgeConfig, TaskSpec


def test_get_judge_returns_codex_for_codex_autorater_backend() -> None:
    judge = get_judge("autorater", JudgeConfig(backend="codex"))

    assert isinstance(judge, CodexJudge)


def test_get_judge_keeps_claude_code_as_default_autorater_backend() -> None:
    judge = get_judge("autorater", JudgeConfig(backend="claude"))

    assert isinstance(judge, ClaudeCodeJudge)


def test_codex_judge_uses_output_last_message(monkeypatch, tmp_path) -> None:
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        output_path = cmd[cmd.index("--output-last-message") + 1]
        with open(output_path, "w") as f:
            json.dump({"passed": True, "reasoning": "The transcript matches."}, f)
        return subprocess.CompletedProcess(cmd, 0, stdout="noisy session log", stderr="")

    monkeypatch.setattr("verdict.judge.codex_judge.shutil.which", lambda _name: "codex.cmd")
    monkeypatch.setattr("verdict.judge.codex_judge.subprocess.run", fake_run)

    passed, reasoning = evaluate_with_codex(
        expect="The assistant says hello.",
        transcript=[ConversationTurn(role="assistant", content="hello")],
        model="gpt-5.4-mini",
        cwd=str(tmp_path),
    )

    assert passed is True
    assert reasoning == "The transcript matches."
    cmd, kwargs = calls[0]
    assert cmd[:2] == ["codex.cmd", "exec"]
    assert cmd[cmd.index("--model") + 1] == "gpt-5.4-mini"
    assert cmd[-1] == "-"
    assert "Respond with valid JSON only" in kwargs["input"]
    assert kwargs["cwd"] == str(tmp_path)


def test_script_judge_uses_codex_for_expect_when_configured(monkeypatch, tmp_path) -> None:
    calls = []

    def fake_eval(*, expect, transcript, model, cwd, timeout=60):
        calls.append((expect, transcript, model, cwd, timeout))
        return True, "Codex accepted the transcript."

    monkeypatch.setattr("verdict.judge.script_assert.evaluate_with_codex", fake_eval)

    judge = ScriptAssertJudge(JudgeConfig(backend="codex", model="gpt-5.4-mini"))
    result = judge.evaluate(
        task=TaskSpec(
            id="task-001",
            name="Combined checks",
            prompt="Say hello",
            expect="The assistant says hello.",
            assert_script="assert True",
        ),
        transcript=[ConversationTurn(role="assistant", content="hello")],
        final_output="hello",
        spec_dir=str(tmp_path),
    )

    assert result.passed is True
    assert result.assert_passed is True
    assert result.autorater_passed is True
    assert result.autorater_reasoning == "Codex accepted the transcript."
    assert calls[0][0] == "The assistant says hello."


def test_codex_judge_extracts_model_error_from_noisy_cli_output() -> None:
    output = """
2026-05-21T02:05:29Z WARN lots of startup noise
OpenAI Codex v0.132.0
ERROR: {"type":"error","status":400,"error":{"type":"invalid_request_error","message":"The 'bad-model' model is not supported when using Codex with a ChatGPT account."}}
"""

    assert _extract_codex_error(output) == (
        "codex judge failed: The 'bad-model' model is not supported when using "
        "Codex with a ChatGPT account."
    )
