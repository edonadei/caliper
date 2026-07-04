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


def test_pi_run_captures_token_usage_end_to_end(monkeypatch, tmp_path) -> None:
    """Smoke test: a full pi run parses per-message usage into AttemptResult.usage.

    The stream shape (``message_end`` assistant events carrying
    ``usage{input,output,cacheRead,cacheWrite}``) mirrors real ``pi --mode json``
    output captured from the live CLI; pi's ``input`` is already non-cached.
    """

    def assistant_msg(text, inp, out, cache_read, cache_write):
        return {
            "type": "message_end",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": text}],
                "api": "anthropic-messages",
                "provider": "anthropic",
                "model": "claude-sonnet-4-6",
                "usage": {
                    "input": inp,
                    "output": out,
                    "cacheRead": cache_read,
                    "cacheWrite": cache_write,
                    "totalTokens": inp + out + cache_read + cache_write,
                    "cost": {"total": 0.01},
                },
            },
        }

    def fake_run(cmd, **kwargs):
        if cmd[1:] == ["--version"]:
            return _version(cmd)
        events = [
            {"type": "session", "version": 3, "id": "abc", "cwd": "/tmp"},
            {"type": "agent_start"},
            {"type": "turn_start"},
            {"type": "message_end", "message": {"role": "user", "content": []}},
            # Two assistant turns; usage is summed across them.
            assistant_msg("Working on it.", 1200, 15, 100, 0),
            assistant_msg("Wrote hello to the file.", 300, 25, 0, 0),
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
        prompt="Write hello to a file",
        skill_path=None,
        model=None,
        timeout=12,
        isolated_home=str(tmp_path),
    )

    assert result.final_output == "Wrote hello to the file."
    assert result.usage is not None
    assert result.usage.input_tokens == 1500  # 1200 + 300, non-cached
    assert result.usage.output_tokens == 40  # 15 + 25
    assert result.usage.cache_read_tokens == 100
    assert result.usage.cache_creation_tokens == 0
    # Disjoint fields: total never double-counts cache.
    assert result.usage.total_tokens == 1640


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
