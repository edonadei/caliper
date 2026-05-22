from __future__ import annotations

import json
import subprocess

import pytest

from caliper.harness.base import HarnessConfigurationError
from caliper.harness.claude_code import ClaudeCodeHarness


def test_claude_harness_accepts_runner_contract_with_extra_path(monkeypatch, tmp_path) -> None:
    skill = tmp_path / "review.md"
    skill.write_text("Review the code.")
    run_calls = []

    def fake_run(cmd, **kwargs):
        run_calls.append((cmd, kwargs))
        stdout = "\n".join(
            [
                json.dumps(
                    {
                        "type": "assistant",
                        "message": {"content": [{"type": "text", "text": "done"}]},
                    }
                ),
                json.dumps({"type": "result", "result": "done"}),
            ]
        )
        return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

    monkeypatch.setattr("caliper.harness.claude_code.subprocess.run", fake_run)

    result = ClaudeCodeHarness(model="claude-test").run(
        task_id="task-001",
        attempt=1,
        prompt="/review the diff",
        skill_path=str(skill),
        model=None,
        timeout=30,
        isolated_home=str(tmp_path / "home"),
        extra_path=[str(tmp_path / "bin")],
    )

    assert result.exit_code == 0
    assert result.final_output == "done"

    cmd, kwargs = next((cmd, kwargs) for cmd, kwargs in run_calls if cmd[:2] == ["claude", "-p"])
    assert cmd[:2] == ["claude", "-p"]
    assert cmd[2] == "/review the diff"
    assert "--dangerously-skip-permissions" in cmd
    assert cmd[cmd.index("--model") + 1] == "claude-test"
    assert kwargs["env"]["PATH"].startswith(str(tmp_path / "bin"))
    assert not list((tmp_path / "home" / ".claude" / "commands").glob("*.md"))


def test_claude_harness_reports_cli_startup_crash_before_auth(monkeypatch, tmp_path) -> None:
    def fake_run(cmd, **kwargs):
        stderr = "\n".join(
            [
                "file:///opt/homebrew/lib/node_modules/@anthropic-ai/claude-code/cli.js:470",
                "TypeError: Cannot read properties of undefined (reading 'prototype')",
                "    at file:///opt/homebrew/lib/node_modules/@anthropic-ai/claude-code/cli.js:470:25515",
                "Node.js v25.1.0",
                "ANTHROPIC_API_KEY",
            ]
        )
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr=stderr)

    monkeypatch.setattr("caliper.harness.claude_code.subprocess.run", fake_run)

    with pytest.raises(HarnessConfigurationError) as exc:
        ClaudeCodeHarness().run(
            task_id="task-001",
            attempt=1,
            prompt="hello",
            skill_path=None,
            model=None,
            timeout=30,
            isolated_home=str(tmp_path / "home"),
            extra_path=[],
        )

    message = str(exc.value)
    assert "Claude CLI crashed during startup" in message
    assert "Node.js v25.1.0" in message
    assert "could not resolve Anthropic authentication" not in message


def test_claude_harness_prefers_even_major_nvm_node(monkeypatch, tmp_path) -> None:
    nvm_root = tmp_path / ".nvm" / "versions" / "node"
    node_22_bin = nvm_root / "v22.12.0" / "bin"
    node_25_bin = nvm_root / "v25.1.0" / "bin"
    node_20_bin = nvm_root / "v20.14.0" / "bin"
    for node_bin in (node_22_bin, node_25_bin, node_20_bin):
        node_bin.mkdir(parents=True)
        (node_bin / "node").write_text("")

    monkeypatch.setattr("caliper.harness.claude_code.Path.home", lambda: tmp_path)
    monkeypatch.setenv("PATH", f"/opt/homebrew/bin:{node_22_bin}:/usr/bin")

    env = ClaudeCodeHarness()._build_env(
        isolated_home=str(tmp_path / "home"),
        extra_path=[],
    )

    assert env["PATH"].split(":")[0] == str(node_22_bin)
