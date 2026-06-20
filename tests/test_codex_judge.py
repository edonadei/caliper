from __future__ import annotations

import json
import subprocess

from caliper.harness.base import ConversationTurn
from caliper.judge.codex_judge import _extract_codex_error, evaluate_with_codex
from caliper.judge.script_assert import EvalJudge
from caliper.schema.spec import JudgeConfig, TaskSpec


def test_eval_judge_always_returns_eval_judge_instance() -> None:
    for backend in ("codex", "claude-code", "claude-api", "openai-api"):
        judge = EvalJudge(JudgeConfig(backend=backend))
        assert isinstance(judge, EvalJudge)


def test_eval_judge_expect_only_calls_llm(monkeypatch, tmp_path) -> None:
    calls = []

    def fake_eval(*, expect, transcript, model, cwd, timeout=60):
        calls.append((expect, model, cwd))
        return True, "Codex accepted the transcript."

    monkeypatch.setattr("caliper.judge.script_assert.EvalJudge._llm_evaluate",
                        lambda self, task, transcript, spec_dir: fake_eval(
                            expect=task.expect,
                            transcript=transcript,
                            model=self._config.model,
                            cwd=spec_dir,
                        ))

    judge = EvalJudge(JudgeConfig(backend="codex", model="test-model"))
    result = judge.evaluate(
        task=TaskSpec(id="t1", name="t", prompt="p", expect="should say hello"),
        transcript=[ConversationTurn(role="assistant", content="hello")],
        final_output="hello",
        spec_dir=str(tmp_path),
    )

    assert result.passed is True
    assert result.assert_passed is None
    assert result.autorater_passed is True


def test_eval_judge_assert_only_runs_script_no_llm(tmp_path) -> None:
    judge = EvalJudge(JudgeConfig(backend="codex"))
    result = judge.evaluate(
        task=TaskSpec(id="t2", name="t", prompt="p", assert_script="assert 1 == 1"),
        transcript=[],
        final_output="",
        spec_dir=str(tmp_path),
    )

    assert result.passed is True
    assert result.assert_passed is True
    assert result.autorater_passed is None


def test_eval_judge_assert_failure_makes_overall_fail(tmp_path) -> None:
    judge = EvalJudge(JudgeConfig(backend="codex"))
    result = judge.evaluate(
        task=TaskSpec(id="t3", name="t", prompt="p", assert_script="assert False, 'nope'"),
        transcript=[],
        final_output="",
        spec_dir=str(tmp_path),
    )

    assert result.passed is False
    assert result.assert_passed is False


def test_eval_judge_both_checks_must_pass(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        "caliper.judge.script_assert.EvalJudge._llm_evaluate",
        lambda self, task, transcript, spec_dir: (True, "LLM says yes"),
    )

    judge = EvalJudge(JudgeConfig(backend="codex"))
    result = judge.evaluate(
        task=TaskSpec(
            id="t4",
            name="t",
            prompt="p",
            expect="pass",
            assert_script="assert False, 'script fails'",
        ),
        transcript=[],
        final_output="",
        spec_dir=str(tmp_path),
    )

    assert result.passed is False
    assert result.autorater_passed is True
    assert result.assert_passed is False


def test_codex_judge_uses_output_last_message(monkeypatch, tmp_path) -> None:
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        output_path = cmd[cmd.index("--output-last-message") + 1]
        with open(output_path, "w") as f:
            json.dump({"mode": "verdict", "passed": True, "reasoning": "The transcript matches."}, f)
        return subprocess.CompletedProcess(cmd, 0, stdout="noisy session log", stderr="")

    monkeypatch.setattr("caliper.judge.codex_judge.shutil.which", lambda _name: "codex.cmd")
    monkeypatch.setattr("caliper.judge.codex_judge.CODEX_APP_CLI", tmp_path / "missing-codex")
    monkeypatch.setattr("caliper.judge.codex_judge.subprocess.run", fake_run)

    passed, reasoning = evaluate_with_codex(
        expect="The assistant says hello.",
        transcript=[ConversationTurn(role="assistant", content="hello")],
        model="test-model",
        cwd=str(tmp_path),
    )

    assert passed is True
    assert reasoning == "The transcript matches."
    cmd, kwargs = calls[0]
    assert cmd[:2] == ["codex.cmd", "exec"]
    assert cmd[cmd.index("--model") + 1] == "test-model"
    assert cmd[-1] == "-"
    assert "Respond with valid JSON only" in kwargs["input"]
    assert kwargs["cwd"] == str(tmp_path)


def test_eval_judge_uses_codex_for_expect_when_configured(monkeypatch, tmp_path) -> None:
    calls = []

    def fake_eval(*, expect, transcript, model, cwd, timeout=60):
        calls.append((expect, transcript, model, cwd, timeout))
        return True, "Codex accepted the transcript."

    monkeypatch.setattr("caliper.judge.codex_judge.evaluate_with_codex", fake_eval)

    judge = EvalJudge(JudgeConfig(backend="codex", model="test-model"))
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
