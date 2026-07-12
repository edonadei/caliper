"""EvalJudge behavior plus the per-backend bare-prompt path.

The autorater rides the backend seam's ``run_prompt`` half, so every backend's
judge path is mocked at the single spawn chokepoint
(``caliper.harness.base.subprocess.run``) rather than per judge module.
"""

from __future__ import annotations

import json
import subprocess

from caliper.harness.base import ConversationTurn
from caliper.harness.codex import _extract_codex_error, CodexHarness
from caliper.harness.hermes import HermesHarness
from caliper.harness.pi import PiHarness
from caliper.judge.script_assert import EvalJudge
from caliper.schema.spec import TaskSpec


def _task(**overrides) -> TaskSpec:
    fields = {"id": "t1", "name": "t", "prompt": "p", "expect": "says ok"}
    fields.update(overrides)
    return TaskSpec(**fields)


def _spawn(monkeypatch, stdout: str = "", returncode: int = 0, stderr: str = ""):
    """Patch the CLI spawn chokepoint; returns the recorded (cmd, kwargs) calls."""
    calls: list[tuple[list[str], dict]] = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return subprocess.CompletedProcess(
            cmd, returncode, stdout=stdout, stderr=stderr
        )

    monkeypatch.setattr("caliper.harness.base.subprocess.run", fake_run)
    return calls


# --- EvalJudge check-combination rules (backend-agnostic) -------------------


def test_eval_judge_always_returns_eval_judge_instance() -> None:
    for backend in ("codex", "claude-code", "pi", "hermes"):
        judge = EvalJudge(backend=backend)
        assert isinstance(judge, EvalJudge)


def test_eval_judge_expect_only_calls_llm(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        "caliper.judge.script_assert.EvalJudge._llm_evaluate",
        lambda self, task, transcript, spec_dir: (
            True,
            "Codex accepted the transcript.",
            False,
            self._model,
        ),
    )

    judge = EvalJudge(backend="codex", model="test-model")
    result = judge.evaluate(
        task=_task(expect="should say hello"),
        transcript=[ConversationTurn(role="assistant", content="hello")],
        final_output="hello",
        spec_dir=str(tmp_path),
    )

    assert result.passed is True
    assert result.assert_passed is None
    assert result.autorater_passed is True


def test_eval_judge_assert_only_runs_script_no_llm(tmp_path) -> None:
    judge = EvalJudge(backend="codex")
    result = judge.evaluate(
        task=_task(expect="", assert_script="assert 1 == 1"),
        transcript=[],
        final_output="",
        spec_dir=str(tmp_path),
    )

    assert result.passed is True
    assert result.assert_passed is True
    assert result.autorater_passed is None


def test_eval_judge_assert_failure_makes_overall_fail(tmp_path) -> None:
    judge = EvalJudge(backend="codex")
    result = judge.evaluate(
        task=_task(expect="", assert_script="assert False, 'nope'"),
        transcript=[],
        final_output="",
        spec_dir=str(tmp_path),
    )

    assert result.passed is False
    assert result.assert_passed is False


def test_eval_judge_both_checks_must_pass(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        "caliper.judge.script_assert.EvalJudge._llm_evaluate",
        lambda self, task, transcript, spec_dir: (True, "LLM says yes", False, None),
    )

    judge = EvalJudge(backend="codex")
    result = judge.evaluate(
        task=_task(expect="pass", assert_script="assert False, 'script fails'"),
        transcript=[],
        final_output="",
        spec_dir=str(tmp_path),
    )

    assert result.passed is False
    assert result.autorater_passed is True
    assert result.assert_passed is False


def _errored_llm(self, task, transcript, spec_dir):
    return False, "judge flaked", True, None


def test_errored_autorater_dropped_when_assert_passes(monkeypatch, tmp_path) -> None:
    # Rule B: a surviving assert verdict stands; the errored autorater is dropped.
    monkeypatch.setattr(
        "caliper.judge.script_assert.EvalJudge._llm_evaluate", _errored_llm
    )
    judge = EvalJudge(backend="codex")
    result = judge.evaluate(
        task=_task(expect="x", assert_script="assert True"),
        transcript=[],
        final_output="",
        spec_dir=str(tmp_path),
    )
    assert result.errored is False
    assert result.passed is True
    assert result.autorater_passed is None


def test_errored_autorater_does_not_override_failing_assert(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setattr(
        "caliper.judge.script_assert.EvalJudge._llm_evaluate", _errored_llm
    )
    judge = EvalJudge(backend="codex")
    result = judge.evaluate(
        task=_task(expect="x", assert_script="assert False"),
        transcript=[],
        final_output="",
        spec_dir=str(tmp_path),
    )
    assert result.errored is False  # assert gave a real (failing) verdict
    assert result.passed is False


def test_judge_error_when_only_check_errors(monkeypatch, tmp_path) -> None:
    # expect-only task whose autorater flakes: no verdict survives -> errored.
    monkeypatch.setattr(
        "caliper.judge.script_assert.EvalJudge._llm_evaluate", _errored_llm
    )
    judge = EvalJudge(backend="codex")
    result = judge.evaluate(
        task=_task(expect="x"),
        transcript=[],
        final_output="",
        spec_dir=str(tmp_path),
    )
    assert result.errored is True
    assert result.passed is False


def test_unknown_judge_backend_errors(tmp_path) -> None:
    judge = EvalJudge(backend="not-a-backend")
    result = judge.evaluate(
        task=_task(),
        transcript=[],
        final_output="",
        spec_dir=str(tmp_path),
    )
    assert result.errored is True
    assert "Unknown judge backend" in result.reasoning


# --- claude-code prompt path -------------------------------------------------


def test_eval_judge_claude_code_invokes_claude_cli(monkeypatch, tmp_path) -> None:
    calls = _spawn(
        monkeypatch,
        stdout='{"mode": "verdict", "passed": true, "reasoning": "The transcript says hello."}',
    )

    result = EvalJudge(backend="claude", model="claude-test").evaluate(
        task=_task(expect="The assistant says hello."),
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
    _spawn(monkeypatch, stdout=json.dumps(envelope))

    # No model requested → the judge should still record what Claude actually used.
    result = EvalJudge(backend="claude-code").evaluate(
        task=_task(),
        transcript=[ConversationTurn(role="assistant", content="ok")],
        final_output="ok",
        spec_dir=str(tmp_path),
    )

    assert result.passed is True
    assert result.autorater_passed is True
    assert result.resolved_model == "claude-opus-4-8"


# --- codex prompt path --------------------------------------------------------


def _codex_cli_present(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("caliper.harness.codex.shutil.which", lambda _name: "codex.cmd")
    monkeypatch.setattr(
        "caliper.harness.codex.CODEX_APP_CLI", tmp_path / "missing-codex"
    )
    monkeypatch.delenv("CODEX_CLI_PATH", raising=False)


def test_codex_judge_uses_output_last_message(monkeypatch, tmp_path) -> None:
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        output_path = cmd[cmd.index("--output-last-message") + 1]
        with open(output_path, "w") as f:
            json.dump(
                {
                    "mode": "verdict",
                    "passed": True,
                    "reasoning": "The transcript matches.",
                },
                f,
            )
        return subprocess.CompletedProcess(
            cmd, 0, stdout="noisy session log", stderr=""
        )

    _codex_cli_present(monkeypatch, tmp_path)
    monkeypatch.setattr("caliper.harness.base.subprocess.run", fake_run)

    result = EvalJudge(backend="codex", model="test-model").evaluate(
        task=_task(expect="The assistant says hello."),
        transcript=[ConversationTurn(role="assistant", content="hello")],
        final_output="hello",
        spec_dir=str(tmp_path),
    )

    assert result.passed is True
    assert result.errored is False
    assert result.autorater_reasoning == "The transcript matches."
    cmd, kwargs = calls[0]
    assert cmd[:2] == ["codex.cmd", "exec"]
    assert cmd[cmd.index("--model") + 1] == "test-model"
    assert cmd[-1] == "-"
    assert "Respond with valid JSON only" in kwargs["input"]
    assert kwargs["cwd"] == str(tmp_path)


def test_codex_missing_cli_errors(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("caliper.harness.codex.shutil.which", lambda _name: None)
    monkeypatch.setattr(
        "caliper.harness.codex.CODEX_APP_CLI", tmp_path / "missing-codex"
    )
    monkeypatch.delenv("CODEX_CLI_PATH", raising=False)

    result = CodexHarness().run_prompt("anything", cwd=str(tmp_path))

    assert result.error is not None
    assert "codex CLI not found" in result.error


def test_codex_error_extraction_from_noisy_cli_output() -> None:
    output = """
2026-05-21T02:05:29Z WARN lots of startup noise
OpenAI Codex v0.132.0
ERROR: {"type":"error","status":400,"error":{"type":"invalid_request_error","message":"The 'bad-model' model is not supported when using Codex with a ChatGPT account."}}
"""

    assert _extract_codex_error(output) == (
        "codex judge failed: The 'bad-model' model is not supported when using "
        "Codex with a ChatGPT account."
    )


# --- hermes prompt path -------------------------------------------------------


def _hermes_cli_present(monkeypatch) -> None:
    monkeypatch.setattr(
        "caliper.harness.hermes.shutil.which", lambda _: "/usr/bin/hermes"
    )
    monkeypatch.delenv("HERMES_CLI_PATH", raising=False)


def test_hermes_backend_is_a_valid_judge(monkeypatch, tmp_path) -> None:
    """Regression: `--judge-model hermes` must dispatch, not 'Unknown judge backend'."""
    _hermes_cli_present(monkeypatch)
    calls = _spawn(
        monkeypatch, stdout='{"mode": "verdict", "passed": true, "reasoning": "ok"}'
    )

    result = EvalJudge(backend="hermes", model="anthropic/claude-sonnet-4.6").evaluate(
        task=_task(),
        transcript=[ConversationTurn(role="assistant", content="ok")],
        final_output="ok",
        spec_dir=str(tmp_path),
    )

    assert result.passed is True
    assert result.autorater_passed is True
    cmd, _ = calls[0]
    assert cmd[0] == "/usr/bin/hermes"
    assert "-z" in cmd
    assert "--ignore-rules" in cmd
    assert cmd[cmd.index("--model") + 1] == "anthropic/claude-sonnet-4.6"


def test_judge_strips_markdown_fence(monkeypatch, tmp_path) -> None:
    _hermes_cli_present(monkeypatch)
    _spawn(
        monkeypatch,
        stdout='```json\n{"mode": "verdict", "passed": false, "reasoning": "no"}\n```',
    )

    result = EvalJudge(backend="hermes").evaluate(
        task=_task(expect="anything"),
        transcript=[],
        final_output="",
        spec_dir=str(tmp_path),
    )

    assert (result.passed, result.errored) == (False, False)
    assert result.autorater_reasoning == "no"


def test_hermes_judge_reports_cli_error_as_errored(monkeypatch, tmp_path) -> None:
    _hermes_cli_present(monkeypatch)
    _spawn(monkeypatch, returncode=1, stderr="not logged in")

    result = HermesHarness().run_prompt("anything", cwd=str(tmp_path))

    assert result.error is not None
    assert "not logged in" in result.error


def test_hermes_judge_missing_cli_errors(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("caliper.harness.hermes.shutil.which", lambda _: None)
    monkeypatch.delenv("HERMES_CLI_PATH", raising=False)

    result = HermesHarness().run_prompt("anything", cwd=str(tmp_path))

    assert result.error is not None
    assert "hermes CLI not found" in result.error


# --- pi prompt path -----------------------------------------------------------


def _pi_stream(text: str) -> str:
    """A minimal pi JSON event stream ending in an assistant message."""
    return json.dumps(
        {
            "type": "message_end",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": text}],
            },
        }
    )


def test_pi_backend_is_a_valid_judge(monkeypatch, tmp_path) -> None:
    """Regression: `--judge-model pi` must dispatch, not report 'Unknown judge backend'."""
    monkeypatch.setattr("caliper.harness.pi.shutil.which", lambda _: "/usr/bin/pi")
    monkeypatch.delenv("PI_CLI_PATH", raising=False)
    calls = _spawn(
        monkeypatch,
        stdout=_pi_stream('{"mode": "verdict", "passed": true, "reasoning": "ok"}'),
    )

    result = EvalJudge(backend="pi", model="claude-sonnet-4-6").evaluate(
        task=_task(),
        transcript=[ConversationTurn(role="assistant", content="ok")],
        final_output="ok",
        spec_dir=str(tmp_path),
    )

    assert result.passed is True
    assert result.autorater_passed is True
    # The judge model reached the CLI (backend:model form resolves through).
    cmd, _ = calls[0]
    assert cmd[cmd.index("--model") + 1] == "claude-sonnet-4-6"
    assert "--print" in cmd and "--mode" in cmd


def test_pi_judge_reports_cli_error_as_errored(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("caliper.harness.pi.shutil.which", lambda _: "/usr/bin/pi")
    monkeypatch.delenv("PI_CLI_PATH", raising=False)
    _spawn(monkeypatch, returncode=1, stderr="not logged in")

    result = PiHarness().run_prompt("anything", cwd=str(tmp_path))

    assert result.error is not None
    assert "not logged in" in result.error


def test_pi_judge_missing_cli_errors(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("caliper.harness.pi.shutil.which", lambda _: None)
    monkeypatch.delenv("PI_CLI_PATH", raising=False)

    result = PiHarness().run_prompt("anything", cwd=str(tmp_path))

    assert result.error is not None
    assert "pi CLI not found" in result.error
