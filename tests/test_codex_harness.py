from __future__ import annotations

import json
import subprocess

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10, where tomllib is not yet stdlib
    import tomli as tomllib

import pytest

from caliper.harness.base import (
    UNLISTED_MCP,
    HarnessConfigurationError,
    ProcessResult,
    RunContext,
)
from caliper.harness.codex import NO_ACCOUNT_CONNECTORS, CodexHarness
from caliper.harness.prompt_failure import PromptFailureKind
from caliper.schema.spec import McpServer
from caliper.skills import resolve_skills

from conftest import patch_cli_calls, run_context


def test_codex_installs_the_skill_and_leaves_the_prompt_alone(
    monkeypatch, tmp_path
) -> None:
    skill_dir = tmp_path / "src"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: careful\ndescription: test\n---\n\nUse caliper carefully."
    )
    refs = resolve_skills([str(skill_dir / "SKILL.md")], tmp_path)
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
        # Codex has no force-load flag and caliper no longer invents one: the
        # prompt on stdin is exactly what the spec authored.
        assert kwargs["input"] == "Validate the spec"
        # The agent works in the attempt workdir, not its config home (#130).
        assert kwargs["cwd"] == str(tmp_path / "work")
        return subprocess.CompletedProcess(cmd, 0, stdout="VALID\n", stderr="")

    monkeypatch.setattr("caliper.harness.base.shutil.which", fake_which)
    monkeypatch.setattr(
        "caliper.harness.codex.CODEX_APP_CLI", tmp_path / "missing-codex"
    )
    patch_cli_calls(monkeypatch, fake_run)

    result = CodexHarness().run(
        run_context(
            prompt="Validate the spec",
            skill_refs=refs,
            model="test-model",
            timeout=30,
            isolated_home=str(tmp_path),
            workdir=str(tmp_path / "work"),
            extra_path=[str(tmp_path / "bin")],
        )
    )

    assert result.exit_code == 0
    assert result.final_output == "VALID"
    # Installed at codex's own skills root, under its frontmatter name.
    assert (tmp_path / ".codex" / "skills" / "careful" / "SKILL.md").exists()
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

    monkeypatch.setattr("caliper.harness.base.shutil.which", lambda _name: "codex")
    monkeypatch.setattr(
        "caliper.harness.codex.CODEX_APP_CLI", tmp_path / "missing-codex"
    )
    patch_cli_calls(monkeypatch, fake_run)

    result = CodexHarness().run(
        run_context(
            prompt="Hello",
            model=None,
            timeout=12,
            isolated_home=str(tmp_path),
        )
    )

    assert result.exit_code == 0
    exec_cmd = calls[1][0]
    assert "--model" not in exec_cmd


def test_codex_cli_disables_account_apps_and_plugins(monkeypatch, tmp_path) -> None:
    # The ChatGPT login behind auth.json carries hosted connectors
    # (mcp__codex_apps__*) and remote plugins that config.toml's mcp_servers
    # can't reach; the attempt must not see them (#129).
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if cmd == ["codex", "--version"]:
            return subprocess.CompletedProcess(
                cmd, 0, stdout="codex-cli 0.145.0\n", stderr=""
            )
        return subprocess.CompletedProcess(cmd, 0, stdout="OK\n", stderr="")

    monkeypatch.setattr("caliper.harness.base.shutil.which", lambda _name: "codex")
    monkeypatch.setattr(
        "caliper.harness.codex.CODEX_APP_CLI", tmp_path / "missing-codex"
    )
    patch_cli_calls(monkeypatch, fake_run)

    CodexHarness().run(
        run_context(prompt="Hello", model=None, timeout=12, isolated_home=str(tmp_path))
    )

    exec_cmd = calls[1]
    overrides = [exec_cmd[i + 1] for i, arg in enumerate(exec_cmd) if arg == "-c"]
    assert "features.apps=false" in overrides
    assert "features.plugins=false" in overrides


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

    monkeypatch.setattr("caliper.harness.base.shutil.which", lambda _name: "codex")
    monkeypatch.setattr(
        "caliper.harness.codex.CODEX_APP_CLI", tmp_path / "missing-codex"
    )
    patch_cli_calls(monkeypatch, fake_run)

    result = CodexHarness().run(
        run_context(
            prompt="Inspect the repo",
            model=None,
            timeout=12,
            isolated_home=str(tmp_path),
        )
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

    monkeypatch.setattr("caliper.harness.base.shutil.which", lambda _name: "codex")
    monkeypatch.setattr(
        "caliper.harness.codex.CODEX_APP_CLI", tmp_path / "missing-codex"
    )
    patch_cli_calls(monkeypatch, fake_run)

    result = CodexHarness().run(
        run_context(
            prompt="Use a tool",
            model=None,
            timeout=12,
            isolated_home=str(tmp_path),
        )
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
    monkeypatch.setattr("caliper.harness.base.shutil.which", lambda _name: "old-codex")

    assert CodexHarness().cli_path() == str(app_cli)


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
        skill_refs=[],
        model=None,
        timeout=12,
        isolated_home=str(isolated_home),
        workdir=str(isolated_home / "work"),
        extra_path=[],
        mcp_servers=None,
    )
    harness = CodexHarness()
    harness._seed_home(ctx)
    harness._prepare(ctx)

    copied = (isolated_home / ".codex" / "config.toml").read_text()
    assert 'model = "gpt-5.5"' not in copied
    assert "model_reasoning_effort" not in copied
    assert "profile-model" not in copied
    assert (isolated_home / ".codex" / "auth.json").exists()


def test_codex_fails_clearly_when_cli_is_not_runnable(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("caliper.harness.base.shutil.which", lambda _name: "codex.exe")
    monkeypatch.setattr(
        "caliper.harness.codex.CODEX_APP_CLI", tmp_path / "missing-codex"
    )

    def fake_run(cmd, **kwargs):
        raise OSError("access denied")

    patch_cli_calls(monkeypatch, fake_run)

    with pytest.raises(HarnessConfigurationError) as exc:
        CodexHarness(model="fallback-model").run(
            run_context(
                prompt="Hello",
                model=None,
                timeout=12,
                isolated_home=str(tmp_path),
            )
        )

    assert "Caliper runs skills only through CLI agents" in str(exc.value)


def test_codex_fails_clearly_when_cli_requires_newer_version(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setattr("caliper.harness.base.shutil.which", lambda _name: "codex")
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

    patch_cli_calls(monkeypatch, fake_run)

    with pytest.raises(HarnessConfigurationError) as exc:
        CodexHarness().run(
            run_context(
                prompt="Hello",
                model="gpt-5.4-mini",
                timeout=12,
                isolated_home=str(tmp_path),
            )
        )

    message = str(exc.value)
    assert "requested model" in message
    assert "upgrade the Codex app or CLI" in message
    assert "Hello" not in message


@pytest.mark.parametrize(
    ("stderr", "expected_message"),
    [
        ("ERROR: boom", "codex judge failed: boom"),
        ("synthetic error without a marker", "codex judge exited 7"),
    ],
)
def test_codex_read_last_message_classifies_nonzero_exit_as_failure(
    tmp_path, stderr, expected_message
) -> None:
    proc = ProcessResult(
        stdout="synthetic partial output",
        stderr=stderr,
        returncode=7,
        timed_out=False,
    )

    result = CodexHarness()._read_last_message(
        proc, "test-model", tmp_path / "missing-output.txt"
    )

    assert result.failure is not None
    assert result.failure.kind is PromptFailureKind.OTHER
    assert result.error == result.failure.message
    assert result.failure.message == expected_message
    assert result.resolved_model == "test-model"
    assert result.text == ""


def test_codex_read_last_message_leaves_success_unclassified(tmp_path) -> None:
    output_path = tmp_path / "last-message.txt"
    output_path.write_text("42\n")
    proc = ProcessResult(stdout="", stderr="", returncode=0, timed_out=False)

    result = CodexHarness()._read_last_message(proc, "gpt-5", output_path)

    assert result.failure is None
    assert result.error is None
    assert result.text == "42"


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


_CHATGPT_AUTH = '{"tokens": {"access_token": "t"}}'
# A ChatGPT login's plugins are unlistable; turned off, the rest is recordable.
_NO_PLUGINS = "[features]\nplugins = false\n"


def _fake_codex_home(tmp_path, config_text: str | None, *, auth: str = "{}"):
    """A fake ~/.codex with auth and (optionally) a config carrying user MCP state."""
    real = tmp_path / "realhome" / ".codex"
    real.mkdir(parents=True)
    (real / "auth.json").write_text(auth)
    if config_text is not None:
        (real / "config.toml").write_text(config_text)
    return tmp_path / "realhome"


def _run_codex_mcp(
    monkeypatch,
    tmp_path,
    mcp_servers,
    *,
    home=None,
    user_customizations=False,
    captured=None,
):
    """Seed an attempt with declared mcp_servers; return the seeded config.toml path.

    ``captured``, when given, receives the attempt's ``cmd`` and ``result``.
    """
    monkeypatch.setattr(
        "caliper.harness.mcp.preflight_stdio_servers", lambda *a, **kw: None
    )
    home = home if home is not None else _fake_codex_home(tmp_path, _AMBIENT_CONFIG)
    iso = tmp_path / "iso"
    iso.mkdir()

    def fake_run(cmd, **kwargs):
        if cmd[1:] == ["--version"]:
            return subprocess.CompletedProcess(
                cmd, 0, stdout="codex-cli 0.142.0\n", stderr=""
            )
        if captured is not None:
            captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="OK\n", stderr="")

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("CODEX_CLI_PATH", raising=False)
    monkeypatch.setattr("caliper.harness.base.shutil.which", lambda _n: "codex")
    monkeypatch.setattr(
        "caliper.harness.codex.CODEX_APP_CLI", tmp_path / "missing-codex"
    )
    patch_cli_calls(monkeypatch, fake_run)

    result = CodexHarness().run(
        run_context(
            prompt="Hello",
            model=None,
            timeout=30,
            isolated_home=str(iso),
            mcp_servers=mcp_servers,
            user_customizations=user_customizations,
        )
    )
    if captured is not None:
        captured["result"] = result
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
    # Isolation strips behavioral settings as well as the top-level model pin.
    assert "approval_policy" not in config
    assert "history" not in config
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
    assert "approval_policy" not in config


def test_codex_writes_config_when_user_has_none(monkeypatch, tmp_path) -> None:
    home = _fake_codex_home(tmp_path, None)  # auth.json only, no config.toml
    seeded = _run_codex_mcp(
        monkeypatch, tmp_path, {"echo": McpServer(command="python3")}, home=home
    )
    config = tomllib.loads(seeded.read_text())
    assert config["mcp_servers"] == {"echo": {"command": "python3"}}


def test_codex_user_customizations_keeps_user_servers_and_connectors(
    monkeypatch, tmp_path
) -> None:
    captured: dict = {}
    seeded = _run_codex_mcp(
        monkeypatch,
        tmp_path,
        {"echo": McpServer(command="python3")},
        home=_fake_codex_home(
            tmp_path, _AMBIENT_CONFIG + _NO_PLUGINS, auth=_CHATGPT_AUTH
        ),
        user_customizations=True,
        captured=captured,
    )
    config = tomllib.loads(seeded.read_text())
    # The user's server survives (with its nested env table) beside the spec's.
    assert config["mcp_servers"] == {
        "personal": {"command": "my-private-server", "env": {"TOKEN": "abc"}},
        "echo": {"command": "python3"},
    }
    # The model pin is still stripped: --user-customizations is about tools only.
    assert "model" not in config
    # The account's hosted apps and plugins are left on.
    assert "features.apps=false" not in captured["cmd"]
    assert "features.plugins=false" not in captured["cmd"]
    # Recorded: the user's server plus the hosted apps, never the declared one.
    assert captured["result"].loaded_user_customizations == [
        "mcp:codex_apps",
        "mcp:personal",
        "settings:config.toml",
    ]


@pytest.mark.parametrize("loads", [True, False])
def test_codex_copies_installed_plugins_only_when_loading(
    monkeypatch, tmp_path, loads
) -> None:
    # Plugins, and the MCP servers they bring, live outside config.toml.
    home = _fake_codex_home(tmp_path, _AMBIENT_CONFIG)
    plugin = home / ".codex" / "plugins" / "cache" / "market" / "cua" / "server.json"
    plugin.parent.mkdir(parents=True)
    plugin.write_text("{}")
    seeded = _run_codex_mcp(
        monkeypatch, tmp_path, None, home=home, user_customizations=loads
    )
    copied = seeded.parent / "plugins" / "cache" / "market" / "cua" / "server.json"
    assert copied.exists() is loads
    assert plugin.exists()


def test_codex_user_customizations_lets_the_spec_win_a_name_clash(
    monkeypatch, tmp_path
) -> None:
    captured: dict = {}
    seeded = _run_codex_mcp(
        monkeypatch,
        tmp_path,
        {"personal": McpServer(command="spec-server")},
        user_customizations=True,
        captured=captured,
    )
    # Parses at all: a table defined twice would be a TOML error.
    config = tomllib.loads(seeded.read_text())
    assert config["mcp_servers"] == {"personal": {"command": "spec-server"}}
    # An API-key login (no OAuth tokens) brings no hosted apps to claim.
    assert captured["result"].loaded_user_customizations == ["settings:config.toml"]


def test_codex_user_customizations_still_ablates_a_server_the_user_also_has(
    monkeypatch, tmp_path
) -> None:
    # `--ablate personal --user-customizations`: the declared `personal` was removed,
    # and the user's own `personal` must not come back in its place.
    monkeypatch.setattr(
        "caliper.harness.mcp.preflight_stdio_servers", lambda *a, **kw: None
    )
    home = _fake_codex_home(tmp_path, _AMBIENT_CONFIG)
    iso = tmp_path / "iso"
    iso.mkdir()
    monkeypatch.setenv("HOME", str(home))
    CodexHarness()._prepare(
        run_context(
            isolated_home=str(iso),
            mcp_servers={},
            spec_mcp_names=frozenset({"personal"}),
            user_customizations=True,
        )
    )
    config = tomllib.loads((iso / ".codex" / "config.toml").read_text())
    assert "mcp_servers" not in config


def test_codex_records_no_hosted_apps_when_the_user_turned_them_off(
    monkeypatch, tmp_path
) -> None:
    home = _fake_codex_home(
        tmp_path,
        _AMBIENT_CONFIG + "\n[features]\napps = false\nplugins = false\n",
        auth=_CHATGPT_AUTH,
    )
    captured: dict = {}
    _run_codex_mcp(
        monkeypatch,
        tmp_path,
        None,
        home=home,
        user_customizations=True,
        captured=captured,
    )
    assert captured["result"].loaded_user_customizations == [
        "mcp:personal",
        "settings:config.toml",
    ]


@pytest.mark.parametrize(
    "customizations, declared, expected",
    [
        (False, frozenset(), NO_ACCOUNT_CONNECTORS),
        (True, frozenset({"echo"}), ()),
        # The hosted apps surface as `codex_apps`, so a spec server of that
        # name, declared or ablated, keeps them off (the spec wins the clash).
        (True, frozenset({"codex_apps"}), ("-c", "features.apps=false")),
    ],
)
def test_codex_connector_overrides(customizations, declared, expected) -> None:
    ctx = run_context(user_customizations=customizations, spec_mcp_names=declared)
    assert CodexHarness._connector_overrides(ctx) == expected


def test_codex_is_not_fooled_by_a_header_inside_a_multiline_value(
    monkeypatch, tmp_path
) -> None:
    home = _fake_codex_home(
        tmp_path,
        "[mcp_servers.personal]\n"
        'command = "mine"\n'
        "env.MESSAGE = '''\n"
        "[history]\n"
        "'''\n"
        "\n"
        "[history]\n"
        'persistence = "none"\n',
    )
    seeded = _run_codex_mcp(monkeypatch, tmp_path, None, home=home)
    assert tomllib.loads(seeded.read_text()) == {}


def test_codex_refuses_an_invalid_user_config(monkeypatch, tmp_path) -> None:
    home = _fake_codex_home(tmp_path, "this is = = not toml\n")
    with pytest.raises(HarnessConfigurationError, match="not valid TOML"):
        _run_codex_mcp(monkeypatch, tmp_path, None, home=home)


def test_codex_records_unknown_when_a_chatgpt_login_keeps_plugins(
    monkeypatch, tmp_path
) -> None:
    # Plugins can bring tools caliper cannot list, so "[]" or a bare partial
    # list would claim an environment the attempt did not have; the staged
    # files are still recorded, beside the unlisted-MCP marker.
    captured: dict = {}
    _run_codex_mcp(
        monkeypatch,
        tmp_path,
        None,
        home=_fake_codex_home(tmp_path, _AMBIENT_CONFIG, auth=_CHATGPT_AUTH),
        user_customizations=True,
        captured=captured,
    )
    assert captured["result"].loaded_user_customizations == [
        UNLISTED_MCP,
        "settings:config.toml",
    ]


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
