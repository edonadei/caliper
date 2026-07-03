from __future__ import annotations

import json
import subprocess

import pytest

from caliper.harness.base import HarnessConfigurationError
from caliper.harness.pi import PiHarness


def _version(cmd):
    return subprocess.CompletedProcess(cmd, 0, stdout="0.80.2\n", stderr="")


def test_pi_cli_receives_skill_and_model_flags(monkeypatch, tmp_path) -> None:
    skill = tmp_path / "SKILL.md"
    skill.write_text("---\ndescription: test\n---\n\nUse caliper carefully.")
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        if cmd[1:] == ["--version"]:
            return _version(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr("caliper.harness.pi.shutil.which", lambda _n: "pi")
    monkeypatch.setattr("caliper.harness.base.subprocess.run", fake_run)

    PiHarness().run(
        task_id="task-001",
        attempt=1,
        prompt="Validate the spec",
        skill_path=str(skill),
        model="claude-sonnet-4-6",
        timeout=30,
        isolated_home=str(tmp_path),
        extra_path=[str(tmp_path / "bin")],
    )

    run_cmd = calls[1][0]
    assert run_cmd[0] == "pi"
    assert "--print" in run_cmd
    assert run_cmd[run_cmd.index("--mode") + 1] == "json"
    assert "--no-session" in run_cmd
    assert "--approve" in run_cmd
    assert calls[1][1]["stdin"] is subprocess.DEVNULL
    assert run_cmd[run_cmd.index("--model") + 1] == "claude-sonnet-4-6"
    assert run_cmd[run_cmd.index("--skill") + 1] == str(skill)
    # prompt is the final positional arg
    assert run_cmd[-1] == "Validate the spec"
    # config is pointed at the per-attempt copy, not the real ~/.pi
    env = calls[1][1]["env"]
    assert env["PI_CODING_AGENT_DIR"] == str(tmp_path / ".pi" / "agent")
    assert env["HOME"] == str(tmp_path)


def test_pi_omits_model_and_skill_when_unspecified(monkeypatch, tmp_path) -> None:
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        if cmd[1:] == ["--version"]:
            return _version(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr("caliper.harness.pi.shutil.which", lambda _n: "pi")
    monkeypatch.setattr("caliper.harness.base.subprocess.run", fake_run)

    PiHarness().run(
        task_id="task-001",
        attempt=1,
        prompt="Hello",
        skill_path=None,
        model=None,
        timeout=12,
        isolated_home=str(tmp_path),
    )

    run_cmd = calls[1][0]
    assert "--model" not in run_cmd
    assert "--skill" not in run_cmd


def test_pi_json_stream_captures_tool_calls(monkeypatch, tmp_path) -> None:
    def fake_run(cmd, **kwargs):
        if cmd[1:] == ["--version"]:
            return _version(cmd)
        events = [
            {"type": "session", "version": 3, "id": "abc", "cwd": "/tmp"},
            {"type": "agent_start"},
            {"type": "turn_start"},
            {
                "type": "tool_execution_start",
                "toolCallId": "toolu_1",
                "toolName": "write",
                "args": {"path": "notes.txt", "content": "HELLO"},
            },
            {
                "type": "tool_execution_end",
                "toolCallId": "toolu_1",
                "toolName": "write",
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": "Successfully wrote 5 bytes to notes.txt",
                        }
                    ]
                },
                "isError": False,
            },
            {
                "type": "message_end",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "All done."}],
                },
            },
            {"type": "turn_end"},
            {"type": "agent_end", "messages": []},
        ]
        stdout = "\n".join(json.dumps(e) for e in events)
        return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

    monkeypatch.setattr("caliper.harness.pi.shutil.which", lambda _n: "pi")
    monkeypatch.setattr("caliper.harness.base.subprocess.run", fake_run)

    result = PiHarness().run(
        task_id="task-001",
        attempt=1,
        prompt="Write a file",
        skill_path=None,
        model=None,
        timeout=12,
        isolated_home=str(tmp_path),
    )

    assert result.final_output == "All done."
    assert [turn.role for turn in result.transcript] == [
        "tool_use",
        "tool_result",
        "assistant",
    ]
    assert result.transcript[0].tool_name == "write"
    # tool_input carries file paths so the cheat-detector can inspect them
    assert result.transcript[0].tool_input == {"path": "notes.txt", "content": "HELLO"}
    assert "Successfully wrote 5 bytes" in result.transcript[1].tool_output


def test_pi_missing_cli_raises_configuration_error(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("caliper.harness.pi.shutil.which", lambda _n: None)
    monkeypatch.delenv("PI_CLI_PATH", raising=False)

    with pytest.raises(HarnessConfigurationError, match="pi CLI is not available"):
        PiHarness().run(
            task_id="task-001",
            attempt=1,
            prompt="Hello",
            skill_path=None,
            model=None,
            timeout=12,
            isolated_home=str(tmp_path),
        )


def test_pi_auth_failure_raises_configuration_error(monkeypatch, tmp_path) -> None:
    def fake_run(cmd, **kwargs):
        if cmd[1:] == ["--version"]:
            return _version(cmd)
        return subprocess.CompletedProcess(
            cmd, 1, stdout="", stderr="Error: not authenticated. Please run /login."
        )

    monkeypatch.setattr("caliper.harness.pi.shutil.which", lambda _n: "pi")
    monkeypatch.setattr("caliper.harness.base.subprocess.run", fake_run)

    with pytest.raises(HarnessConfigurationError, match="authentication"):
        PiHarness().run(
            task_id="task-001",
            attempt=1,
            prompt="Hello",
            skill_path=None,
            model=None,
            timeout=12,
            isolated_home=str(tmp_path),
        )
