from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from caliper.harness.base import ConversationTurn
from caliper.judge.errors import (
    format_model_unavailable_message,
    is_model_unavailable,
    judge_failure_reason,
)
from caliper.judge.script_assert import EvalJudge
from caliper.schema.spec import DEFAULT_JUDGE_MODEL, TaskSpec


def test_is_model_unavailable_anthropic_not_found_error() -> None:
    raw = (
        "API Error: 404\n"
        '{"type":"error","error":{"type":"not_found_error",'
        '"message":"model: claude-sonnet-4-20250514"}}'
    )
    assert is_model_unavailable(raw)


def test_is_model_unavailable_openai_style() -> None:
    assert is_model_unavailable("ERROR: model_not_found: gpt-5-codex does not exist")


def test_is_model_unavailable_rejects_unrelated_unparseable() -> None:
    assert not is_model_unavailable('{"mode": "verdict", "passed": true}')
    assert not is_model_unavailable("model.py does not exist")


def test_format_model_unavailable_message_includes_hint() -> None:
    raw = (
        '{"type":"error","error":{"type":"not_found_error",'
        '"message":"model: claude-sonnet-5"}}'
    )
    message = format_model_unavailable_message(raw, DEFAULT_JUDGE_MODEL)
    assert "claude-sonnet-5" in message
    assert "--judge-model" in message


def test_judge_failure_reason_preserves_generic_message() -> None:
    raw = "not json at all"
    assert judge_failure_reason(raw, None).startswith(
        "Judge returned unparseable response:"
    )


def _task(**overrides) -> TaskSpec:
    fields = {"id": "t1", "name": "t", "prompt": "p", "expect": "says ok"}
    fields.update(overrides)
    return TaskSpec(**fields)


def _spawn(monkeypatch, stdout: str = "", returncode: int = 0, stderr: str = ""):
    calls: list[tuple[list[str], dict]] = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return subprocess.CompletedProcess(
            cmd, returncode, stdout=stdout, stderr=stderr
        )

    monkeypatch.setattr("caliper.harness.base.subprocess.run", fake_run)
    return calls


def _codex_cli_present(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("caliper.harness.codex.shutil.which", lambda _name: "codex.cmd")
    monkeypatch.setattr(
        "caliper.harness.codex.CODEX_APP_CLI", tmp_path / "missing-codex"
    )
    monkeypatch.delenv("CODEX_CLI_PATH", raising=False)


def test_eval_judge_maps_json_only_model_error(monkeypatch, tmp_path) -> None:
    stdout = (
        '{"type":"error","error":{"type":"not_found_error",'
        '"message":"model: claude-sonnet-4-20250514"}}'
    )
    _spawn(monkeypatch, stdout=stdout)
    judge = EvalJudge(backend="claude-code")
    result = judge.evaluate(
        task=_task(expect="x"),
        transcript=[],
        final_output="",
        spec_dir=str(tmp_path),
    )
    assert result.errored is True
    assert "--judge-model" in result.autorater_reasoning


@pytest.mark.parametrize(
    "stdout",
    [
        (
            "API Error: 404\n"
            '{"type":"error","error":{"type":"not_found_error",'
            '"message":"model: claude-sonnet-4-20250514"}}'
        ),
    ],
)
def test_eval_judge_maps_model_unavailable_to_actionable_error(
    monkeypatch, tmp_path, stdout: str
) -> None:
    _spawn(monkeypatch, stdout=stdout)
    judge = EvalJudge(backend="claude-code")
    result = judge.evaluate(
        task=_task(expect="The assistant says hello."),
        transcript=[ConversationTurn(role="assistant", content="hello")],
        final_output="hello",
        spec_dir=str(tmp_path),
    )
    assert result.errored is True
    assert "--judge-model" in result.autorater_reasoning
    assert DEFAULT_JUDGE_MODEL in result.autorater_reasoning


def test_eval_judge_codex_harness_error_maps_model_unavailable(
    monkeypatch, tmp_path
) -> None:
    def fake_run(cmd, **kwargs):
        output_path = cmd[cmd.index("--output-last-message") + 1]
        Path(output_path).write_text("")
        return subprocess.CompletedProcess(
            cmd,
            1,
            stdout="",
            stderr='ERROR: {"error":{"message":"The model `gpt-5-codex` does not exist"}}',
        )

    _codex_cli_present(monkeypatch, tmp_path)
    monkeypatch.setattr("caliper.harness.base.subprocess.run", fake_run)
    judge = EvalJudge(backend="codex", model="gpt-5-codex")
    result = judge.evaluate(
        task=_task(expect="x"),
        transcript=[],
        final_output="",
        spec_dir=str(tmp_path),
    )
    assert result.errored is True
    assert "--judge-model" in result.autorater_reasoning
    assert "gpt-5-codex" in result.autorater_reasoning
