from __future__ import annotations

import json
import subprocess
import tomllib

import pytest

from caliper.harness.base import HarnessConfigurationError, RunContext
from caliper.harness.codex import CodexHarness
from caliper.schema.spec import McpServer


def test_codex_cli_receives_injected_skill_on_stdin(monkeypatch, tmp_path) -> None:
    skill = tmp_path / "SKILL.md"
    skill.write_text("---\ndescription: test\n---\n\nUse caliper carefully.")
    calls = []

    def fake_which(name: str) -> str:
        assert name == "codex"
        return "codex.cmd"

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        if cmd == ["codex.cmd", "--version"]:
            return subprocess.CompletedProcess(
                cmd, 0, stdout="codex-cli 0.132.0\n", stderr=""
            )
        assert cmd[:2] == ["codex.cmd", "exec"]
        assert cmd[-1] == "-"
        assert kwargs["input"].startswith("[Skill context]\nUse caliper carefully.")
        assert kwargs["input"].endswith("\n\nValidate the spec")
        assert kwargs["cwd"] == str(tmp_path)
        return subprocess.CompletedProcess(cmd, 0, stdout="VALID\n", stderr="")

    monkeypatch.setattr("caliper.harness.codex.shutil.which", fake_which)
    monkeypatch.setattr(
        "caliper.harness.codex.CODEX_APP_CLI", tmp_path / "missing-codex"
    )
    monkeypatch.setattr("caliper.harness.base.subprocess.run", fake_run)

    result = CodexHarness().run(
        task_id="task-001",
        attempt=1,
        prompt="Validate the spec",
        skill_path=str(skill),
        model="test-model",
        timeout=30,
        isolated_home=str(tmp_path),
        extra_path=[str(tmp_path / "bin")],
    )

    assert result.exit_code == 0
    assert result.final_output == "VALID"
    exec_cmd = calls[1][0]
    assert "--model" in exec_cmd
    assert exec_cmd[exec_cmd.index("--model") + 1] == "test-model"
    assert "--skip-git-repo-check" in exec_cmd
    assert "--dangerously-bypass-approvals-and-sandbox" in exec_cmd
    assert "--json" in exec_cmd


def test_codex_cli_omits_model_when_unspecified(monkeypatch, tmp_path) -> None:
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        if cmd == ["codex", "--version"]:
            return subprocess.CompletedProcess(
                cmd, 0, stdout="codex-cli 0.132.0\n", stderr=""
            )
        return subprocess.CompletedProcess(cmd, 0, stdout="OK\n", stderr="")

    monkeypatch.setattr("caliper.harness.codex.shutil.which", lambda _name: "codex")
    monkeypatch.setattr(
        "caliper.harness.codex.CODEX_APP_CLI", tmp_path / "missing-codex"
    )
    monkeypatch.setattr("caliper.harness.base.subprocess.run", fake_run)

    result = CodexHarness().run(
        task_id="task-001",
        attempt=1,
        prompt="Hello",
        skill_path=None,
        model=None,
        timeout=12,
        isolated_home=str(tmp_path),
    )

    assert result.exit_code == 0
    exec_cmd = calls[1][0]
    assert "--model" not in exec_cmd


def test_codex_json_stream_captures_tool_calls(monkeypatch, tmp_path) -> None:
    def fake_run(cmd, **kwargs):
        if cmd == ["codex", "--version"]:
            return subprocess.CompletedProcess(
                cmd, 0, stdout="codex-cli 0.132.0\n", stderr=""
            )
        events = [
            {"type": "thread.started", "thread_id": "thread-1"},
            {"type": "turn.started"},
            {
                "type": "item.started",
                "item": {
                    "id": "item_0",
                    "type": "command_execution",
                    "command": "/bin/zsh -lc pygount --format=summary .",
                    "aggregated_output": "",
                    "exit_code": None,
                    "status": "in_progress",
                },
            },
            {
                "type": "item.completed",
                "item": {
                    "id": "item_0",
                    "type": "command_execution",
                    "command": "/bin/zsh -lc pygount --format=summary .",
                    "aggregated_output": "Python 1 2 0 0\n",
                    "exit_code": 0,
                    "status": "completed",
                },
            },
            {
                "type": "item.completed",
                "item": {"id": "item_1", "type": "agent_message", "text": "done"},
            },
            {"type": "turn.completed", "usage": {}},
        ]
        stdout = "\n".join(json.dumps(event) for event in events)
        return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

    monkeypatch.setattr("caliper.harness.codex.shutil.which", lambda _name: "codex")
    monkeypatch.setattr(
        "caliper.harness.codex.CODEX_APP_CLI", tmp_path / "missing-codex"
    )
    monkeypatch.setattr("caliper.harness.base.subprocess.run", fake_run)

    result = CodexHarness().run(
        task_id="task-001",
        attempt=1,
        prompt="Inspect the repo",
        skill_path=None,
        model=None,
        timeout=12,
        isolated_home=str(tmp_path),
    )

    assert result.final_output == "done"
    assert [turn.role for turn in result.transcript] == [
        "tool_use",
        "tool_result",
        "assistant",
    ]
    assert result.transcript[0].tool_name == "shell"
    assert result.transcript[0].tool_input == {
        "command": "/bin/zsh -lc pygount --format=summary ."
    }
    assert "Python 1 2 0 0" in result.transcript[1].tool_output
    assert "exit_code=0" in result.transcript[1].tool_output


def test_codex_json_stream_keeps_unknown_tool_items(monkeypatch, tmp_path) -> None:
    def fake_run(cmd, **kwargs):
        if cmd == ["codex", "--version"]:
            return subprocess.CompletedProcess(
                cmd, 0, stdout="codex-cli 0.132.0\n", stderr=""
            )
        events = [
            {
                "type": "item.completed",
                "item": {
                    "id": "item_0",
                    "type": "mcp_tool_call",
                    "name": "lookup",
                    "arguments": {"query": "caliper"},
                    "result": "found",
                },
            },
            {
                "type": "item.completed",
                "item": {"id": "item_1", "type": "agent_message", "text": "done"},
            },
        ]
        stdout = "\n".join(json.dumps(event) for event in events)
        return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

    monkeypatch.setattr("caliper.harness.codex.shutil.which", lambda _name: "codex")
    monkeypatch.setattr(
        "caliper.harness.codex.CODEX_APP_CLI", tmp_path / "missing-codex"
    )
    monkeypatch.setattr("caliper.harness.base.subprocess.run", fake_run)

    result = CodexHarness().run(
        task_id="task-001",
        attempt=1,
        prompt="Use a tool",
        skill_path=None,
        model=None,
        timeout=12,
        isolated_home=str(tmp_path),
    )

    assert result.final_output == "done"
    assert result.transcript[0].role == "tool_use"
    assert result.transcript[0].tool_name == "mcp_tool_call"
    assert result.transcript[0].tool_input["name"] == "lookup"


def test_codex_prefers_app_bundled_cli(monkeypatch, tmp_path) -> None:
    app_cli = tmp_path / "Codex.app" / "Contents" / "Resources" / "codex"
    app_cli.parent.mkdir(parents=True)
    app_cli.write_text("")

    monkeypatch.setattr("caliper.harness.codex.CODEX_APP_CLI", app_cli)
    monkeypatch.setattr("caliper.harness.codex.shutil.which", lambda _name: "old-codex")

    assert CodexHarness()._codex_command() == str(app_cli)


def test_codex_config_copy_strips_top_level_model(monkeypatch, tmp_path) -> None:
    real_home = tmp_path / "real"
    real_codex_home = real_home / ".codex"
    real_codex_home.mkdir(parents=True)
    (real_codex_home / "auth.json").write_text("{}")
    (real_codex_home / "config.toml").write_text(
        "\n".join(
            [
                'model = "gpt-5.5"',
                'model_reasoning_effort = "medium"',
                "",
                "[profiles.keep]",
                'model = "profile-model"',
            ]
        )
        + "\n"
    )

    isolated_home = tmp_path / "isolated"
    monkeypatch.setattr("caliper.harness.codex.Path.home", lambda: real_home)

    ctx = RunContext(
        task_id="task-001",
        attempt=1,
        prompt="Hello",
        skill_path=None,
        model=None,
        timeout=12,
        isolated_home=str(isolated_home),
        extra_path=[],
        mcp_servers=None,
    )
    CodexHarness()._copy_codex_config(ctx)

    copied = (isolated_home / ".codex" / "config.toml").read_text()
    assert 'model = "gpt-5.5"' not in copied
    assert 'model_reasoning_effort = "medium"' in copied
    assert 'model = "profile-model"' in copied
    assert (isolated_home / ".codex" / "auth.json").exists()


def test_codex_fails_clearly_when_cli_is_not_runnable(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("caliper.harness.codex.shutil.which", lambda _name: "codex.exe")
    monkeypatch.setattr(
        "caliper.harness.codex.CODEX_APP_CLI", tmp_path / "missing-codex"
    )

    def fake_run(cmd, **kwargs):
        raise OSError("access denied")

    monkeypatch.setattr("caliper.harness.base.subprocess.run", fake_run)

    with pytest.raises(HarnessConfigurationError) as exc:
        CodexHarness(model="fallback-model").run(
            task_id="task-001",
            attempt=1,
            prompt="Hello",
            skill_path=None,
            model=None,
            timeout=12,
            isolated_home=str(tmp_path),
        )

    assert "Caliper runs skills only through CLI agents" in str(exc.value)


def test_codex_fails_clearly_when_cli_requires_newer_version(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setattr("caliper.harness.codex.shutil.which", lambda _name: "codex")
    monkeypatch.setattr(
        "caliper.harness.codex.CODEX_APP_CLI", tmp_path / "missing-codex"
    )

    def fake_run(cmd, **kwargs):
        if cmd == ["codex", "--version"]:
            return subprocess.CompletedProcess(
                cmd, 0, stdout="codex-cli 0.46.0\n", stderr=""
            )
        return subprocess.CompletedProcess(
            cmd,
            1,
            stdout="",
            stderr=(
                "ERROR: unexpected status 400 Bad Request: "
                '{"detail":"The \'gpt-5.4-mini\' model requires a newer version '
                'of Codex. Please upgrade to the latest app or CLI and try again."}'
            ),
        )

    monkeypatch.setattr("caliper.harness.base.subprocess.run", fake_run)

    with pytest.raises(HarnessConfigurationError) as exc:
        CodexHarness().run(
            task_id="task-001",
            attempt=1,
            prompt="Hello",
            skill_path=None,
            model="gpt-5.4-mini",
            timeout=12,
            isolated_home=str(tmp_path),
        )

    message = str(exc.value)
    assert "requested model" in message
    assert "upgrade the Codex app or CLI" in message
    assert "Hello" not in message


_AMBIENT_CONFIG = (
    'model = "gpt-5"\n'
    'approval_policy = "never"\n'
    "\n"
    "[mcp_servers.personal]\n"
    'command = "my-private-server"\n'
    "\n"
    "[mcp_servers.personal.env]\n"
    'TOKEN = "abc"\n'
    "\n"
    "[history]\n"
    'persistence = "none"\n'
)


def _fake_codex_home(tmp_path, config_text: str | None):
    """A fake ~/.codex with auth and (optionally) a config carrying user MCP state."""
    real = tmp_path / "realhome" / ".codex"
    real.mkdir(parents=True)
    (real / "auth.json").write_text("{}")
    if config_text is not None:
        (real / "config.toml").write_text(config_text)
    return tmp_path / "realhome"


def _run_codex_mcp(monkeypatch, tmp_path, mcp_servers, *, home=None):
    """Seed an attempt with declared mcp_servers; return the seeded config.toml path."""
    home = home if home is not None else _fake_codex_home(tmp_path, _AMBIENT_CONFIG)
    iso = tmp_path / "iso"
    iso.mkdir()

    def fake_run(cmd, **kwargs):
        if cmd[1:] == ["--version"]:
            return subprocess.CompletedProcess(
                cmd, 0, stdout="codex-cli 0.142.0\n", stderr=""
            )
        return subprocess.CompletedProcess(cmd, 0, stdout="OK\n", stderr="")

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("CODEX_CLI_PATH", raising=False)
    monkeypatch.setattr("caliper.harness.codex.shutil.which", lambda _n: "codex")
    monkeypatch.setattr(
        "caliper.harness.codex.CODEX_APP_CLI", tmp_path / "missing-codex"
    )
    monkeypatch.setattr("caliper.harness.base.subprocess.run", fake_run)

    CodexHarness().run(
        task_id="task-001",
        attempt=1,
        prompt="Hello",
        skill_path=None,
        model=None,
        timeout=30,
        isolated_home=str(iso),
        mcp_servers=mcp_servers,
    )
    return iso / ".codex" / "config.toml"


def test_codex_supports_mcp() -> None:
    assert CodexHarness.supports_mcp is True


def test_codex_translates_stdio_and_strips_ambient_servers(
    monkeypatch, tmp_path
) -> None:
    seeded = _run_codex_mcp(
        monkeypatch,
        tmp_path,
        {
            "echo": McpServer(
                command="python3", args=["/tmp/echo.py"], env={"DEBUG": "1"}
            )
        },
    )
    config = tomllib.loads(seeded.read_text())
    # The declared server replaces the user's ambient `personal` server wholesale.
    assert config["mcp_servers"] == {
        "echo": {"command": "python3", "args": ["/tmp/echo.py"], "env": {"DEBUG": "1"}}
    }
    assert "personal" not in config["mcp_servers"]
    # Non-MCP config survives the strip, but the top-level model pin is dropped.
    assert config["approval_policy"] == "never"
    assert config["history"] == {"persistence": "none"}
    assert "model" not in config


def test_codex_translates_remote_header_auth_and_interpolates(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv("MCP_TOKEN", "s3cr3t")
    seeded = _run_codex_mcp(
        monkeypatch,
        tmp_path,
        {
            "gdrive": McpServer(
                type="http",
                url="https://mcp.example.com/gdrive",
                headers={"Authorization": "Bearer ${MCP_TOKEN}"},
            )
        },
    )
    config = tomllib.loads(seeded.read_text())
    # Remote becomes {url, http_headers} (no `transport`, which codex infers from
    # url); the secret is resolved at the boundary so a literal token lands here.
    assert config["mcp_servers"] == {
        "gdrive": {
            "url": "https://mcp.example.com/gdrive",
            "http_headers": {"Authorization": "Bearer s3cr3t"},
        }
    }


def test_codex_removes_ambient_servers_when_spec_declares_none(
    monkeypatch, tmp_path
) -> None:
    seeded = _run_codex_mcp(monkeypatch, tmp_path, None)
    config = tomllib.loads(seeded.read_text())
    # A no-MCP eval must not inherit the user's personal servers, but keeps the rest.
    assert "mcp_servers" not in config
    assert config["approval_policy"] == "never"


def test_codex_writes_config_when_user_has_none(monkeypatch, tmp_path) -> None:
    home = _fake_codex_home(tmp_path, None)  # auth.json only, no config.toml
    seeded = _run_codex_mcp(
        monkeypatch, tmp_path, {"echo": McpServer(command="python3")}, home=home
    )
    config = tomllib.loads(seeded.read_text())
    assert config["mcp_servers"] == {"echo": {"command": "python3"}}


def test_codex_errors_on_unset_mcp_env_var(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("MCP_TOKEN", raising=False)
    with pytest.raises(HarnessConfigurationError, match="MCP_TOKEN"):
        _run_codex_mcp(
            monkeypatch,
            tmp_path,
            {
                "gdrive": McpServer(
                    type="http",
                    url="https://mcp.example.com/gdrive",
                    headers={"Authorization": "Bearer ${MCP_TOKEN}"},
                )
            },
        )


def test_codex_secret_config_is_locked_down(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("MCP_TOKEN", "s3cr3t")
    seeded = _run_codex_mcp(
        monkeypatch,
        tmp_path,
        {
            "gdrive": McpServer(
                type="http",
                url="https://mcp.example.com/gdrive",
                headers={"Authorization": "Bearer ${MCP_TOKEN}"},
            )
        },
    )
    # The config now holds a resolved secret, so it must not be world/group-readable.
    assert (seeded.stat().st_mode & 0o077) == 0


def test_codex_parses_mcp_tool_call_as_doubled_underscore_name() -> None:
    # The exact event shape codex exec --json emits for an MCP tool call: an
    # item.started then item.completed of type mcp_tool_call carrying server/tool
    # and a structured `result`. Only the completed item is turned into turns.
    stream = "\n".join(
        [
            json.dumps({"item": {"type": "agent_message", "text": "looking it up"}}),
            json.dumps(
                {
                    "type": "item.started",
                    "item": {
                        "type": "mcp_tool_call",
                        "server": "echo",
                        "tool": "secret_word",
                        "arguments": {},
                        "result": None,
                        "status": "in_progress",
                    },
                }
            ),
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {
                        "type": "mcp_tool_call",
                        "server": "echo",
                        "tool": "secret_word",
                        "arguments": {},
                        "result": {
                            "content": [{"type": "text", "text": "caliper"}],
                            "structured_content": None,
                        },
                        "error": None,
                        "status": "completed",
                    },
                }
            ),
            json.dumps({"item": {"type": "agent_message", "text": "done"}}),
        ]
    )
    transcript, final = CodexHarness()._parse_stream(stream)
    tool_names = [t.tool_name for t in transcript if t.role == "tool_use"]
    # The in-progress item.started must not produce a second, duplicate turn.
    assert tool_names.count("mcp__echo__secret_word") == 1
    outputs = [t.tool_output for t in transcript if t.role == "tool_result"]
    assert any("caliper" in (o or "") for o in outputs)
    assert final == "done"
