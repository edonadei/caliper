from __future__ import annotations

import json
import subprocess

from caliper.harness.base import ProcessResult
from caliper.harness.claude_code import (
    ClaudeCodeHarness,
    _classify_claude_prompt_failure,
)
from caliper.harness.base import ConversationTurn
from caliper.harness.prompt_failure import (
    PromptFailureKind,
    classify_claude_api_error_status,
    format_judge_failure,
)
from caliper.judge.script_assert import EvalJudge
from caliper.schema.spec import DEFAULT_JUDGE_MODEL, TaskSpec

# Recorded from `claude -p "say ok" --output-format json --model claude-sonnet-4-20250514`
# against a retired model (issue #75).
RETIRED_MODEL_ENVELOPE = {
    "type": "result",
    "subtype": "success",
    "is_error": True,
    "api_error_status": 404,
    "terminal_reason": "api_error",
    "result": (
        "There's an issue with the selected model (claude-sonnet-4-20250514). "
        "It may not exist or you may not have access to it."
    ),
    "modelUsage": {},
}


def test_classify_claude_api_error_status_maps_observed_codes() -> None:
    assert classify_claude_api_error_status(404) is PromptFailureKind.MODEL_UNAVAILABLE
    assert classify_claude_api_error_status(401) is PromptFailureKind.AUTH
    assert classify_claude_api_error_status(429) is PromptFailureKind.RATE_LIMITED
    assert classify_claude_api_error_status(500) is None


def test_classify_claude_prompt_failure_uses_recorded_fixture() -> None:
    stdout = json.dumps(RETIRED_MODEL_ENVELOPE)
    result = _classify_claude_prompt_failure(stdout, DEFAULT_JUDGE_MODEL)

    assert result is not None
    assert result.text == ""
    assert result.failure is not None
    assert result.failure.kind is PromptFailureKind.MODEL_UNAVAILABLE
    assert result.failure.status == 404
    assert "claude-sonnet-4-20250514" in result.failure.message
    # The harness carries the raw provider message; the judge formats it.
    assert result.error == result.failure.message


def test_classify_claude_prompt_failure_leaves_unclassified_is_error_alone() -> None:
    envelope = {
        **RETIRED_MODEL_ENVELOPE,
        "api_error_status": 500,
        "result": "temporary upstream failure",
    }
    assert _classify_claude_prompt_failure(json.dumps(envelope), None) is None


def test_claude_prompt_output_classifies_in_harness_not_downstream(
    monkeypatch,
) -> None:
    stdout = json.dumps(RETIRED_MODEL_ENVELOPE)

    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 1, stdout=stdout, stderr="")

    monkeypatch.setattr("caliper.harness.base.subprocess.run", fake_run)
    monkeypatch.setattr(
        "caliper.harness.claude_code.shutil.which", lambda _name: "claude"
    )

    result = ClaudeCodeHarness(model=DEFAULT_JUDGE_MODEL).run_prompt(
        "anything", cwd="."
    )

    assert result.failure is not None
    assert result.failure.kind is PromptFailureKind.MODEL_UNAVAILABLE
    assert result.text == ""


def test_claude_prompt_output_unclassified_is_error_passes_text_through(
    monkeypatch,
) -> None:
    envelope = {
        "type": "result",
        "is_error": True,
        "api_error_status": 500,
        "result": '{"mode": "verdict", "passed": false, "reasoning": "refused"}',
        "modelUsage": {},
    }
    stdout = json.dumps(envelope)

    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

    monkeypatch.setattr("caliper.harness.base.subprocess.run", fake_run)
    monkeypatch.setattr(
        "caliper.harness.claude_code.shutil.which", lambda _name: "claude"
    )

    result = ClaudeCodeHarness().run_prompt("anything", cwd=".")

    assert result.failure is None
    assert result.error is None
    assert '"mode": "verdict"' in result.text


def _task(**overrides) -> TaskSpec:
    fields = {"id": "t1", "name": "t", "prompt": "p", "expect": "says ok"}
    fields.update(overrides)
    return TaskSpec(**fields)


def test_eval_judge_surfaces_classified_model_unavailable(
    monkeypatch, tmp_path
) -> None:
    stdout = json.dumps(RETIRED_MODEL_ENVELOPE)

    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 1, stdout=stdout, stderr="")

    monkeypatch.setattr("caliper.harness.base.subprocess.run", fake_run)
    monkeypatch.setattr(
        "caliper.harness.claude_code.shutil.which", lambda _name: "claude"
    )

    result = EvalJudge(backend="claude-code").evaluate(
        task=_task(expect="x"),
        transcript=[ConversationTurn(role="assistant", content="hello")],
        final_output="hello",
        spec_dir=str(tmp_path),
    )

    assert result.errored is True
    assert "--judge-model" in (result.autorater_reasoning or "")
    assert DEFAULT_JUDGE_MODEL in (result.autorater_reasoning or "")
    assert "unparseable" not in (result.autorater_reasoning or "").lower()


def test_format_judge_failure_auth_includes_hint() -> None:
    from caliper.harness.prompt_failure import PromptFailure

    message = format_judge_failure(
        PromptFailure(kind=PromptFailureKind.AUTH, message="invalid token", status=401),
        "claude-opus-4-8",
    )
    assert "authentication failed" in message
    assert "--judge-model" in message


def test_claude_harness_does_not_classify_without_envelope(monkeypatch) -> None:
    result = _classify_claude_prompt_failure("plain text", None)
    assert result is None

    harness_result = ClaudeCodeHarness()._prompt_output(
        ProcessResult(stdout="plain text", stderr="", returncode=0, timed_out=False),
        None,
        {},
    )
    assert harness_result.failure is None
    assert harness_result.text == "plain text"
