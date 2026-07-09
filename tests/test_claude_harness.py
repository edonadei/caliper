from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from caliper.harness.base import HarnessConfigurationError
from caliper.harness.claude_code import ClaudeCodeHarness


def _ok_stream(cmd: list[str]) -> subprocess.CompletedProcess:
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


def test_claude_harness_accepts_runner_contract_with_extra_path(
    monkeypatch, tmp_path
) -> None:
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

    monkeypatch.setattr("caliper.harness.base.subprocess.run", fake_run)

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

    cmd, kwargs = next(
        (cmd, kwargs) for cmd, kwargs in run_calls if cmd[:2] == ["claude", "-p"]
    )
    assert cmd[:2] == ["claude", "-p"]
    assert cmd[2] == "/review the diff"
    assert "--dangerously-skip-permissions" in cmd
    assert cmd[cmd.index("--model") + 1] == "claude-test"
    assert kwargs["env"]["PATH"].startswith(str(tmp_path / "bin"))
    assert not list((tmp_path / "home" / ".claude" / "commands").glob("*.md"))


def test_claude_harness_reports_cli_startup_crash_before_auth(
    monkeypatch, tmp_path
) -> None:
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

    monkeypatch.setattr("caliper.harness.base.subprocess.run", fake_run)

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


def test_claude_harness_materializes_mcp_config(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("MCP_API_TOKEN", "sk-secret")
    captured: dict = {}

    def fake_run(cmd, **kwargs):
        # _prepare may shell out (e.g. macOS keychain); only inspect the agent spawn.
        if cmd[0] != "claude":
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="")
        captured["cmd"] = cmd
        idx = cmd.index("--mcp-config")
        path = Path(cmd[idx + 1])
        captured["path"] = path
        # The config exists while the agent runs and holds the resolved secret.
        captured["config"] = json.loads(path.read_text())
        return _ok_stream(cmd)

    monkeypatch.setattr("caliper.harness.base.subprocess.run", fake_run)
    home = tmp_path / "home"
    home.mkdir()

    ClaudeCodeHarness().run(
        task_id="task-001",
        attempt=1,
        prompt="p",
        skill_path=None,
        model=None,
        timeout=30,
        isolated_home=str(home),
        extra_path=[],
        mcp_servers={
            "echo": {
                "command": "python3",
                "args": ["s.py"],
                "env": {"API_TOKEN": "${MCP_API_TOKEN}"},
            }
        },
    )

    cmd = captured["cmd"]
    # --strict-mcp-config so the attempt sees only the declared servers.
    assert "--strict-mcp-config" in cmd
    assert captured["config"] == {
        "mcpServers": {
            "echo": {
                "command": "python3",
                "args": ["s.py"],
                "env": {"API_TOKEN": "sk-secret"},
            }
        }
    }
    # The secret-bearing config is removed once the attempt finishes.
    assert not captured["path"].exists()


def test_claude_harness_omits_mcp_flags_when_no_servers(monkeypatch, tmp_path) -> None:
    captured: dict = {}

    def fake_run(cmd, **kwargs):
        if cmd[0] != "claude":
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="")
        captured["cmd"] = cmd
        return _ok_stream(cmd)

    monkeypatch.setattr("caliper.harness.base.subprocess.run", fake_run)
    home = tmp_path / "home"
    home.mkdir()

    ClaudeCodeHarness().run(
        task_id="task-001",
        attempt=1,
        prompt="p",
        skill_path=None,
        model=None,
        timeout=30,
        isolated_home=str(home),
        extra_path=[],
    )

    assert "--mcp-config" not in captured["cmd"]
    assert "--strict-mcp-config" not in captured["cmd"]


def test_claude_harness_errors_on_unset_mcp_env_var(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("MCP_API_TOKEN", raising=False)

    def fake_run(cmd, **kwargs):
        # _prepare may shell out before _command; the agent itself must not spawn.
        if cmd[0] == "claude":
            raise AssertionError("agent must not spawn when an MCP env var is unset")
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="")

    monkeypatch.setattr("caliper.harness.base.subprocess.run", fake_run)
    home = tmp_path / "home"
    home.mkdir()

    with pytest.raises(HarnessConfigurationError, match="MCP_API_TOKEN"):
        ClaudeCodeHarness().run(
            task_id="task-001",
            attempt=1,
            prompt="p",
            skill_path=None,
            model=None,
            timeout=30,
            isolated_home=str(home),
            extra_path=[],
            mcp_servers={
                "echo": {
                    "command": "python3",
                    "args": [],
                    "env": {"API_TOKEN": "${MCP_API_TOKEN}"},
                }
            },
        )
