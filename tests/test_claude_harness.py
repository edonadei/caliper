from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from caliper.harness.base import HarnessConfigurationError
from caliper.harness.base import ProcessResult
from caliper.harness.claude_code import ClaudeCodeHarness
from caliper.schema.spec import McpServer
from caliper.skills import resolve_skills

from conftest import patch_cli_calls, run_context


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
    skill_dir = tmp_path / "src"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: reviewer\ndescription: Reviews code.\n---\n\nReview the code."
    )
    refs = resolve_skills([str(skill_dir / "SKILL.md")], tmp_path)
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

    patch_cli_calls(monkeypatch, fake_run)

    result = ClaudeCodeHarness(model="claude-test").run(
        run_context(
            prompt="Review the diff",
            skill_refs=refs,
            model=None,
            timeout=30,
            isolated_home=str(tmp_path / "home"),
            extra_path=[str(tmp_path / "bin")],
        )
    )

    assert result.exit_code == 0
    assert result.final_output == "done"

    # Installed where the CLI classifies a real skill, under its frontmatter
    # name — not written into .claude/commands/ under a mangled filename, and
    # never pasted into the prompt.
    home = tmp_path / "home"
    assert (home / ".claude" / "skills" / "reviewer" / "SKILL.md").exists()
    assert not (home / ".claude" / "commands").exists()

    cmd, kwargs = next(
        (cmd, kwargs) for cmd, kwargs in run_calls if cmd[:2] == ["claude", "-p"]
    )
    assert cmd[:2] == ["claude", "-p"]
    # The prompt reaches the agent verbatim — no skill text, no invocation.
    assert cmd[2] == "Review the diff"
    assert "--dangerously-skip-permissions" in cmd
    assert cmd[cmd.index("--model") + 1] == "claude-test"
    assert kwargs["env"]["PATH"].startswith(str(tmp_path / "bin"))


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

    patch_cli_calls(monkeypatch, fake_run)

    with pytest.raises(HarnessConfigurationError) as exc:
        ClaudeCodeHarness().run(
            run_context(
                prompt="hello",
                model=None,
                timeout=30,
                isolated_home=str(tmp_path / "home"),
                extra_path=[],
            )
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

    env = ClaudeCodeHarness()._environment(
        run_context(isolated_home=str(tmp_path / "home"))
    )

    assert env["PATH"].split(":")[0] == str(node_22_bin)


def test_claude_harness_forwards_the_oauth_token_past_the_api_key_guard(
    monkeypatch, tmp_path
) -> None:
    """A seeded credential file withholds the API keys but not the OAuth token.

    The guard exists so an unfunded key cannot override valid OAuth, which makes
    it the wrong side of the fence for the token `claude setup-token` mints: that
    token *is* the OAuth auth, and the stripped HOME leaves a headless run
    nothing else to present.
    """
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    (home / ".claude" / ".credentials.json").write_text("{}")

    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "oauth-token")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "unfunded-key")

    env = ClaudeCodeHarness()._environment(run_context(isolated_home=str(home)))

    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "oauth-token"
    assert "ANTHROPIC_API_KEY" not in env


def test_claude_harness_materializes_mcp_config(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("MCP_API_TOKEN", "sk-secret")
    # This test inspects config rendering with a fake agent process; MCP
    # initialization itself is covered by test_mcp.py using real subprocesses.
    monkeypatch.setattr(
        "caliper.harness.mcp.preflight_stdio_servers", lambda *a, **kw: None
    )
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

    patch_cli_calls(monkeypatch, fake_run)
    home = tmp_path / "home"
    home.mkdir()

    ClaudeCodeHarness().run(
        run_context(
            prompt="p",
            model=None,
            timeout=30,
            isolated_home=str(home),
            extra_path=[],
            mcp_servers={
                "echo": McpServer(
                    command="python3",
                    args=["s.py"],
                    env={"API_TOKEN": "${MCP_API_TOKEN}"},
                )
            },
        )
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


def test_claude_harness_materializes_remote_mcp_config(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("GDRIVE_TOKEN", "ya29.secret")
    captured: dict = {}

    def fake_run(cmd, **kwargs):
        if cmd[0] != "claude":
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="")
        idx = cmd.index("--mcp-config")
        captured["path"] = Path(cmd[idx + 1])
        captured["config"] = json.loads(captured["path"].read_text())
        return _ok_stream(cmd)

    patch_cli_calls(monkeypatch, fake_run)
    home = tmp_path / "home"
    home.mkdir()

    ClaudeCodeHarness().run(
        run_context(
            prompt="p",
            model=None,
            timeout=30,
            isolated_home=str(home),
            extra_path=[],
            mcp_servers={
                "gdrive": McpServer(
                    type="http",
                    url="https://mcp.example.com/gdrive",
                    headers={"Authorization": "Bearer ${GDRIVE_TOKEN}"},
                )
            },
        )
    )

    # Emitted in Claude Code's remote shape, with the auth header resolved.
    assert captured["config"] == {
        "mcpServers": {
            "gdrive": {
                "type": "http",
                "url": "https://mcp.example.com/gdrive",
                "headers": {"Authorization": "Bearer ya29.secret"},
            }
        }
    }
    # The secret-bearing config is removed once the attempt finishes.
    assert not captured["path"].exists()


def test_claude_harness_errors_on_unset_remote_header_var(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.delenv("GDRIVE_TOKEN", raising=False)

    def fake_run(cmd, **kwargs):
        if cmd[0] == "claude":
            raise AssertionError("agent must not spawn when an MCP env var is unset")
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="")

    patch_cli_calls(monkeypatch, fake_run)
    home = tmp_path / "home"
    home.mkdir()

    with pytest.raises(HarnessConfigurationError, match="GDRIVE_TOKEN"):
        ClaudeCodeHarness().run(
            run_context(
                prompt="p",
                model=None,
                timeout=30,
                isolated_home=str(home),
                extra_path=[],
                mcp_servers={
                    "gdrive": McpServer(
                        type="http",
                        url="https://mcp.example.com/gdrive",
                        headers={"Authorization": "Bearer ${GDRIVE_TOKEN}"},
                    )
                },
            )
        )


def test_claude_harness_isolates_to_zero_servers_without_mcp_block(
    monkeypatch, tmp_path
) -> None:
    # No mcp: block must not leave the account's claude.ai connectors in play
    # (#129): the attempt sees an empty, strict server set.
    captured: dict = {}

    def fake_run(cmd, **kwargs):
        if cmd[0] != "claude":
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="")
        captured["cmd"] = cmd
        idx = cmd.index("--mcp-config")
        captured["config"] = json.loads(Path(cmd[idx + 1]).read_text())
        return _ok_stream(cmd)

    patch_cli_calls(monkeypatch, fake_run)
    home = tmp_path / "home"
    home.mkdir()

    ClaudeCodeHarness().run(
        run_context(
            prompt="p",
            model=None,
            timeout=30,
            isolated_home=str(home),
            extra_path=[],
        )
    )

    assert "--strict-mcp-config" in captured["cmd"]
    assert captured["config"] == {"mcpServers": {}}


def test_claude_harness_keeps_strict_mcp_when_every_server_is_ablated(
    monkeypatch, tmp_path
) -> None:
    # An empty declared set is not "no mcp: block": the attempt must see zero
    # servers, not whatever the seeded user config happens to carry.
    captured: dict = {}

    def fake_run(cmd, **kwargs):
        if cmd[0] != "claude":
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="")
        captured["cmd"] = cmd
        idx = cmd.index("--mcp-config")
        captured["path"] = Path(cmd[idx + 1])
        captured["config"] = json.loads(captured["path"].read_text())
        return _ok_stream(cmd)

    patch_cli_calls(monkeypatch, fake_run)
    home = tmp_path / "home"
    home.mkdir()

    ClaudeCodeHarness().run(
        run_context(
            prompt="p",
            model=None,
            timeout=30,
            isolated_home=str(home),
            extra_path=[],
            mcp_servers={},
        )
    )

    assert "--strict-mcp-config" in captured["cmd"]
    assert captured["config"] == {"mcpServers": {}}
    assert not captured["path"].exists()


def _init_stream(cmd: list[str], servers: list[str]) -> subprocess.CompletedProcess:
    """An ok stream that opens with the CLI's ``init`` event naming ``servers``."""
    init = json.dumps(
        {
            "type": "system",
            "subtype": "init",
            "mcp_servers": [{"name": n, "status": "connected"} for n in servers],
        }
    )
    ok = _ok_stream(cmd)
    ok.stdout = init + "\n" + ok.stdout
    return ok


def test_claude_harness_user_customizations_merge_with_the_spec_winning(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setattr(
        "caliper.harness.mcp.preflight_stdio_servers", lambda *a, **kw: None
    )
    real_home = tmp_path / "real"
    real_home.mkdir()
    user_config = {
        "mcpServers": {
            "echo": {"command": "user-echo"},
            "personal": {"command": "mine"},
        },
        "theme": "dark",
    }
    (real_home / ".claude.json").write_text(json.dumps(user_config))
    monkeypatch.setattr("caliper.harness.claude_code.Path.home", lambda: real_home)
    home = tmp_path / "home"
    home.mkdir()
    captured: dict = {}

    def fake_run(cmd, **kwargs):
        if cmd[0] != "claude":
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="")
        captured["cmd"] = cmd
        captured["config"] = json.loads(
            Path(cmd[cmd.index("--mcp-config") + 1]).read_text()
        )
        captured["seeded"] = json.loads((home / ".claude.json").read_text())
        return _init_stream(cmd, ["claude.ai Gmail", "echo", "personal"])

    patch_cli_calls(monkeypatch, fake_run)
    result = ClaudeCodeHarness().run(
        run_context(
            prompt="p",
            isolated_home=str(home),
            mcp_servers={"echo": McpServer(command="python3")},
            user_customizations=True,
        )
    )

    # Merged rather than exclusive; the spec's `echo` comes via --mcp-config
    # and the user's is gone from the isolated copy, never from the real file.
    assert "--strict-mcp-config" not in captured["cmd"]
    assert captured["config"] == {"mcpServers": {"echo": {"command": "python3"}}}
    assert captured["seeded"]["mcpServers"] == {"personal": {"command": "mine"}}
    assert json.loads((real_home / ".claude.json").read_text()) == user_config
    # Recorded off the init event, the declared server excluded.
    assert result.loaded_user_customizations == ["mcp:claude.ai Gmail", "mcp:personal"]


def test_claude_harness_records_unknown_without_an_init_event() -> None:
    proc = ProcessResult(
        stdout='{"type": "result"}', stderr="", returncode=0, timed_out=False
    )
    ctx = run_context(user_customizations=True)
    assert ClaudeCodeHarness()._loaded_user_customizations(proc, ctx) is None


def test_claude_harness_user_customizations_still_ablates_a_server_the_user_also_has(
    tmp_path,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    (home / ".claude.json").write_text(
        json.dumps({"mcpServers": {"github": {"command": "mine"}}})
    )
    ClaudeCodeHarness()._drop_shadowed_user_servers(
        run_context(
            isolated_home=str(home),
            mcp_servers={},
            spec_mcp_names=frozenset({"github"}),
            user_customizations=True,
        )
    )
    assert json.loads((home / ".claude.json").read_text()) == {"mcpServers": {}}


def test_claude_harness_errors_on_unset_mcp_env_var(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("MCP_API_TOKEN", raising=False)

    def fake_run(cmd, **kwargs):
        # _prepare may shell out before _command; the agent itself must not spawn.
        if cmd[0] == "claude":
            raise AssertionError("agent must not spawn when an MCP env var is unset")
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="")

    patch_cli_calls(monkeypatch, fake_run)
    home = tmp_path / "home"
    home.mkdir()

    with pytest.raises(HarnessConfigurationError, match="MCP_API_TOKEN"):
        ClaudeCodeHarness().run(
            run_context(
                prompt="p",
                model=None,
                timeout=30,
                isolated_home=str(home),
                extra_path=[],
                mcp_servers={
                    "echo": McpServer(
                        command="python3",
                        args=[],
                        env={"API_TOKEN": "${MCP_API_TOKEN}"},
                    )
                },
            )
        )


def test_tool_results_streamed_as_user_turns_are_captured():
    # Claude Code's stream-json returns each tool's output inside a `user`
    # event; before this was parsed, every claude-code transcript silently
    # lost its tool results.
    stream = "\n".join(
        json.dumps(e)
        for e in [
            {"type": "user", "message": {"content": [{"type": "text", "text": "go"}]}},
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "toolu_1",
                            "name": "mcp__deployments__lookup",
                            "input": {"service": "checkout-api"},
                        }
                    ]
                },
            },
            {
                "type": "user",
                "message": {
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "toolu_1",
                            "content": [
                                {"type": "text", "text": '{"ticket": "CHG-1"}'}
                            ],
                        }
                    ]
                },
            },
            {
                "type": "user",
                "message": {
                    "content": [
                        {"type": "tool_result", "tool_use_id": "t2", "content": "ok"}
                    ]
                },
            },
            {"type": "result", "result": "Ticket CHG-1."},
        ]
    )
    transcript, final = ClaudeCodeHarness()._parse_stream(stream)
    assert [(t.role, t.tool_output) for t in transcript] == [
        ("tool_use", None),
        ("tool_result", '{"ticket": "CHG-1"}'),
        ("tool_result", "ok"),
    ]
    assert final == "Ticket CHG-1."


def test_claude_harness_stops_the_run_on_an_unavailable_model(
    monkeypatch, tmp_path
) -> None:
    """A model the API does not know fails every attempt the same way: stop."""

    # Recorded from `claude -p ... --output-format stream-json --verbose
    # --model claude-bogus-9` (issue #139), trimmed to the fields read.
    def fake_run(cmd, **kwargs):
        message = (
            "There's an issue with the selected model (claude-bogus-9). It may "
            "not exist or you may not have access to it. Run --model to pick a "
            "different model."
        )
        stdout = "\n".join(
            [
                json.dumps({"type": "system", "subtype": "init"}),
                json.dumps(
                    {
                        "type": "assistant",
                        "message": {
                            "model": "<synthetic>",
                            "content": [{"type": "text", "text": message}],
                        },
                    }
                ),
                json.dumps(
                    {
                        "type": "result",
                        "subtype": "success",
                        "is_error": True,
                        "api_error_status": 404,
                        "terminal_reason": "api_error",
                        "result": message,
                    }
                ),
            ]
        )
        return subprocess.CompletedProcess(
            cmd, 1, stdout=stdout, stderr="[claude-code:unrecognized_model]"
        )

    patch_cli_calls(monkeypatch, fake_run)

    with pytest.raises(HarnessConfigurationError) as exc:
        ClaudeCodeHarness(model="claude-bogus-9").run(
            run_context(
                prompt="hello",
                model=None,
                timeout=30,
                isolated_home=str(tmp_path / "home"),
                extra_path=[],
            )
        )

    message = str(exc.value)
    assert "claude-bogus-9" in message
    assert "--model" in message


def test_claude_harness_leaves_an_agent_writing_about_a_404_alone(
    monkeypatch, tmp_path
) -> None:
    """Only the CLI's own error envelope stops the run, not the agent's words."""

    def fake_run(cmd, **kwargs):
        stdout = "\n".join(
            [
                json.dumps(
                    {
                        "type": "assistant",
                        "message": {
                            "content": [
                                {"type": "text", "text": "The API returned 404."}
                            ]
                        },
                    }
                ),
                json.dumps({"type": "result", "result": "The API returned 404."}),
            ]
        )
        return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

    patch_cli_calls(monkeypatch, fake_run)

    result = ClaudeCodeHarness().run(
        run_context(
            prompt="hello",
            model=None,
            timeout=30,
            isolated_home=str(tmp_path / "home"),
            extra_path=[],
        )
    )

    assert result.final_output == "The API returned 404."


@pytest.mark.parametrize(
    "platform, keychain, seeded, expected",
    [
        # macOS: a Keychain entry wins over a possibly stale file (#180).
        ("darwin", "keychain", "stale-file", "keychain"),
        ("darwin", None, "file", "file"),
        ("darwin", "keychain", None, "keychain"),
        ("darwin", None, None, None),
        # Elsewhere the file is the only source.
        ("linux", "keychain", "file", "file"),
    ],
)
def test_claude_harness_credentials_source(
    monkeypatch, tmp_path, platform, keychain, seeded, expected
) -> None:
    real = tmp_path / "real"
    (real / ".claude").mkdir(parents=True)
    if seeded is not None:
        (real / ".claude" / ".credentials.json").write_text(seeded)
    monkeypatch.setattr(Path, "home", lambda: real)
    monkeypatch.setattr("caliper.harness.claude_code.sys.platform", platform)
    harness = ClaudeCodeHarness()
    monkeypatch.setattr(harness, "_capture_output", lambda cmd, timeout: keychain)
    ctx = run_context(isolated_home=str(tmp_path / "iso"))

    harness._seed_home(ctx)
    harness._prepare(ctx)

    creds = harness._credentials_file(ctx)
    assert (creds.read_text() if creds.exists() else None) == expected


@pytest.mark.parametrize(
    "message",
    [
        "Failed to authenticate: OAuth session expired and could not be refreshed",
        "Failed to authenticate",
        "OAuth session expired",
        "Please run /login",
        "Invalid API key",
    ],
)
@pytest.mark.parametrize("channel", ["result", "stdout", "stderr"])
def test_claude_harness_expired_login_is_configuration_error(
    monkeypatch, tmp_path, message, channel
):

    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(
            cmd,
            1,
            stdout=(
                json.dumps({"type": "result", "is_error": True, "result": message})
                if channel == "result"
                else message
                if channel == "stdout"
                else ""
            ),
            stderr=message if channel == "stderr" else "",
        )

    patch_cli_calls(monkeypatch, fake_run)
    with pytest.raises(HarnessConfigurationError) as exc:
        ClaudeCodeHarness().run(run_context(isolated_home=str(tmp_path / "home")))

    assert message in str(exc.value)
    assert "Run `claude`, then `/login`" in str(exc.value)
    assert "retry" in str(exc.value)


def test_claude_harness_bare_401_is_configuration_error(monkeypatch, tmp_path):
    def fake_run(cmd, **kwargs):
        envelope = {
            "type": "result",
            "is_error": True,
            "api_error_status": 401,
            "result": "Request failed",
        }
        return subprocess.CompletedProcess(
            cmd, 1, stdout=json.dumps(envelope), stderr=""
        )

    patch_cli_calls(monkeypatch, fake_run)
    with pytest.raises(HarnessConfigurationError) as exc:
        ClaudeCodeHarness().run(run_context(isolated_home=str(tmp_path / "home")))

    assert "API error 401: Request failed" in str(exc.value)
    assert "Run `claude`, then `/login`" in str(exc.value)


@pytest.mark.parametrize("exit_code", [0, 1])
def test_claude_harness_agent_discussing_expired_login_is_not_configuration_error(
    monkeypatch, tmp_path, exit_code
):
    message = "Failed to authenticate: OAuth session expired. Please run /login. Invalid API key."

    def fake_run(cmd, **kwargs):
        stdout = "\n".join(
            [
                json.dumps(
                    {
                        "type": "assistant",
                        "message": {"content": [{"type": "text", "text": message}]},
                    }
                ),
                json.dumps({"type": "result", "is_error": False, "result": message}),
            ]
        )
        return subprocess.CompletedProcess(cmd, exit_code, stdout=stdout, stderr="")

    patch_cli_calls(monkeypatch, fake_run)
    result = ClaudeCodeHarness().run(run_context(isolated_home=str(tmp_path / "home")))
    assert result.final_output == message
    assert result.refusal is None
