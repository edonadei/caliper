from __future__ import annotations

import json
import subprocess

import pytest

from caliper.harness.base import HarnessConfigurationError
from caliper.harness.codex import CodexHarness
from caliper.harness.openai_api import OpenAIAPIHarness


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
    monkeypatch.setattr("caliper.harness.codex.subprocess.run", fake_run)

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
    monkeypatch.setattr("caliper.harness.codex.subprocess.run", fake_run)

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
    monkeypatch.setattr("caliper.harness.codex.subprocess.run", fake_run)

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
    monkeypatch.setattr("caliper.harness.codex.subprocess.run", fake_run)

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

    CodexHarness()._copy_codex_config(str(isolated_home))

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

    monkeypatch.setattr("caliper.harness.codex.subprocess.run", fake_run)

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

    assert "does not fall back to the OpenAI API" in str(exc.value)


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

    monkeypatch.setattr("caliper.harness.codex.subprocess.run", fake_run)

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


def test_openai_api_backend_is_explicit(monkeypatch, tmp_path) -> None:
    api_calls = []

    def fake_api(self, prompt: str, model: str, timeout: int):
        api_calls.append((prompt, model, timeout))
        return "api ok", 0, None

    monkeypatch.setattr(OpenAIAPIHarness, "_run_api", fake_api)

    result = OpenAIAPIHarness(model="api-model").run(
        task_id="task-001",
        attempt=1,
        prompt="Hello",
        skill_path=None,
        model=None,
        timeout=12,
        isolated_home=str(tmp_path),
    )

    assert result.exit_code == 0
    assert result.final_output == "api ok"
    assert api_calls == [("Hello", "api-model", 12)]
