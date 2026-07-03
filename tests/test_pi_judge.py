from __future__ import annotations

import json
import subprocess

from caliper.harness.base import ConversationTurn
from caliper.judge.pi_judge import evaluate_with_pi
from caliper.judge.script_assert import EvalJudge
from caliper.schema.spec import TaskSpec


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
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(
            cmd,
            0,
            stdout=_pi_stream('{"mode": "verdict", "passed": true, "reasoning": "ok"}'),
            stderr="",
        )

    monkeypatch.setattr("caliper.judge.pi_judge.shutil.which", lambda _: "/usr/bin/pi")
    monkeypatch.setattr("caliper.judge.pi_judge.subprocess.run", fake_run)

    result = EvalJudge(backend="pi", model="claude-sonnet-4-6").evaluate(
        task=TaskSpec(id="t1", name="t", prompt="p", expect="says ok"),
        transcript=[ConversationTurn(role="assistant", content="ok")],
        final_output="ok",
        spec_dir=str(tmp_path),
    )

    assert result.passed is True
    assert result.autorater_passed is True
    # The judge model reached the CLI (backend:model form resolves through).
    cmd = captured["cmd"]
    assert cmd[cmd.index("--model") + 1] == "claude-sonnet-4-6"
    assert "--print" in cmd and "--mode" in cmd


def test_pi_judge_reports_cli_error_as_errored(monkeypatch, tmp_path) -> None:
    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="not logged in")

    monkeypatch.setattr("caliper.judge.pi_judge.shutil.which", lambda _: "/usr/bin/pi")
    monkeypatch.setattr("caliper.judge.pi_judge.subprocess.run", fake_run)

    passed, reasoning, errored = evaluate_with_pi(
        expect="anything",
        transcript=[],
        model=None,
        spec_dir=str(tmp_path),
    )

    assert passed is False
    assert errored is True
    assert "not logged in" in reasoning


def test_pi_judge_missing_cli_errors(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("caliper.judge.pi_judge.shutil.which", lambda _: None)
    monkeypatch.delenv("PI_CLI_PATH", raising=False)

    passed, reasoning, errored = evaluate_with_pi(
        expect="anything", transcript=[], model=None, spec_dir=str(tmp_path)
    )

    assert (passed, errored) == (False, True)
    assert "pi CLI not found" in reasoning
