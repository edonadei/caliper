from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path

import pytest
from conftest import patch_cli_calls, run_context

from caliper.harness.base import HarnessConfigurationError, LoginRequired, ProcessResult
from caliper.harness.opencode import OPENCODE_CONFIG, OpenCodeHarness
from caliper.harness.refusal import RefusalKind
from caliper.schema.spec import McpServer, PlantedShellCall, PlantedTurn

# Events as opencode 1.19.5 wrote them for a read of note.txt and an answer,
# trimmed to the fields caliper reads.
_STREAM = [
    {"type": "step_start", "part": {"type": "step-start"}},
    {
        "type": "tool_use",
        "part": {
            "type": "tool",
            "tool": "read",
            "callID": "call_1",
            "state": {
                "status": "completed",
                "input": {"filePath": "/work/note.txt"},
                "output": "1: hello caliper",
            },
        },
    },
    {
        "type": "step_finish",
        "part": {
            "type": "step-finish",
            "reason": "tool-calls",
            "tokens": {
                "total": 25196,
                "input": 23299,
                "output": 105,
                "reasoning": 0,
                "cache": {"write": 0, "read": 1792},
            },
        },
    },
    {"type": "step_start", "part": {"type": "step-start"}},
    {"type": "text", "part": {"type": "text", "text": "hello caliper"}},
    {
        "type": "step_finish",
        "part": {
            "type": "step-finish",
            "reason": "stop",
            "tokens": {
                "total": 25311,
                "input": 218,
                "output": 5,
                "reasoning": 3,
                "cache": {"write": 7, "read": 25088},
            },
        },
    },
]


def _stdout(events: list[dict]) -> str:
    return "\n".join(json.dumps(event) for event in events)


def _spawned(monkeypatch, tmp_path, stdout="", **ctx):
    """Run one attempt against a fake opencode; every call it made, in order."""
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        if cmd[1:] == ["--version"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="1.19.5\n", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

    monkeypatch.setattr("caliper.harness.base.shutil.which", lambda _n, **_: "opencode")
    # A declared server is written, never started.
    monkeypatch.setattr(
        "caliper.harness.mcp.preflight_stdio_servers", lambda *a, **kw: None
    )
    patch_cli_calls(monkeypatch, fake_run)
    (tmp_path / "work").mkdir(exist_ok=True)
    fields = {
        "isolated_home": str(tmp_path / "home"),
        "workdir": str(tmp_path / "work"),
        **ctx,
    }
    result = OpenCodeHarness(model=fields.pop("engine", None)).run(
        run_context(**fields)
    )
    return calls, result


def test_opencode_takes_the_prompt_on_stdin_and_runs_unattended(
    monkeypatch, tmp_path
) -> None:
    calls, _ = _spawned(monkeypatch, tmp_path, prompt="Fix the bug", model="a/b")

    cmd, kwargs = calls[-1]
    assert cmd[:2] == ["opencode", "run"]
    assert cmd[cmd.index("--format") + 1] == "json"
    assert cmd[cmd.index("--model") + 1] == "a/b"
    assert "--auto" in cmd
    # A fixed title: no small-model call to name the session.
    assert cmd[cmd.index("--title") + 1] == "caliper"
    # An argv prompt containing a space reaches the agent in quotes.
    assert "Fix the bug" not in cmd
    assert kwargs["input"] == "Fix the bug"
    env = kwargs["env"]
    assert env["HOME"] == str(tmp_path / "home")
    assert env["OPENCODE_DISABLE_AUTOUPDATE"] == "1"
    assert "XDG_CONFIG_HOME" not in env


def test_opencode_names_no_model_when_none_is_asked_for(monkeypatch, tmp_path) -> None:
    calls, _ = _spawned(monkeypatch, tmp_path)

    assert "--model" not in calls[-1][0]


def test_opencode_reads_turns_answer_and_summed_usage(monkeypatch, tmp_path) -> None:
    _, result = _spawned(monkeypatch, tmp_path, stdout=_stdout(_STREAM))

    assert [turn.role for turn in result.transcript] == [
        "tool_use",
        "tool_result",
        "assistant",
    ]
    assert result.transcript[0].tool_name == "read"
    assert result.transcript[0].tool_input == {"filePath": "/work/note.txt"}
    assert result.transcript[1].tool_output == "1: hello caliper"
    assert result.final_output == "hello caliper"
    usage = result.usage
    assert usage.input_tokens == 23299 + 218
    # Reasoning is counted apart from output, and folded back in.
    assert usage.output_tokens == 105 + 5 + 3
    assert usage.cache_read_tokens == 1792 + 25088
    assert usage.cache_creation_tokens == 7


def test_an_mcp_tool_reads_under_its_servers_qualified_name(
    monkeypatch, tmp_path
) -> None:
    """opencode spells it ``<server>_<tool>``; the longest declared server wins."""
    calls = [
        {"type": "tool_use", "part": {"tool": tool, "state": {"output": "ok"}}}
        for tool in ("mail_archive_read", "mail_send", "todowrite")
    ]
    servers = {
        "mail": McpServer(command="python3"),
        "mail.archive": McpServer(command="python3"),
    }

    _, result = _spawned(
        monkeypatch, tmp_path, stdout=_stdout(calls), mcp_servers=servers
    )

    assert [t.tool_name for t in result.transcript if t.role == "tool_use"] == [
        "mcp__mail.archive__read",
        "mcp__mail__send",
        "todowrite",
    ]


def test_declared_servers_are_written_in_opencodes_shape(monkeypatch, tmp_path) -> None:
    servers = {
        "echo": McpServer(command="python3", args=["echo.py"], env={"K": "v"}),
        "web": McpServer(type="http", url="https://x/mcp", headers={"A": "b"}),
    }

    _spawned(monkeypatch, tmp_path, mcp_servers=servers)

    home = tmp_path / "home" / ".config" / "opencode"
    assert OPENCODE_CONFIG.read(home)["mcp"] == {
        "echo": {
            "type": "local",
            "command": ["python3", "echo.py"],
            "environment": {"K": "v"},
        },
        "web": {"type": "remote", "url": "https://x/mcp", "headers": {"A": "b"}},
    }
    if os.name == "posix":
        assert stat.S_IMODE((home / "opencode.json").stat().st_mode) == 0o600


def test_an_isolated_attempt_keeps_only_the_users_providers_and_model(
    monkeypatch, tmp_path
) -> None:
    real = Path.home() / ".config" / "opencode"
    real.mkdir(parents=True)
    # opencode accepts comments and trailing commas in its config.
    (real / "opencode.json").write_text(
        """{
          // the user's own
          "model": "openai/gpt-6.1-terra",
          "provider": {"local": {"options": {"baseURL": "http://localhost:1234/v1"}}},
          "plugin": ["opencode-notifier"],
          "mcp": {"mine": {"type": "local", "command": ["mine"]}}, // a comma, then a note
        }"""
    )

    _spawned(monkeypatch, tmp_path)

    config = OPENCODE_CONFIG.read(tmp_path / "home" / ".config" / "opencode")
    assert config == {
        "model": "openai/gpt-6.1-terra",
        "provider": {"local": {"options": {"baseURL": "http://localhost:1234/v1"}}},
    }


def test_the_login_is_seeded_from_the_xdg_data_home(monkeypatch, tmp_path) -> None:
    data = tmp_path / "data"
    (data / "opencode").mkdir(parents=True)
    (data / "opencode" / "auth.json").write_text('{"openai": {"type": "oauth"}}')
    monkeypatch.setenv("XDG_DATA_HOME", str(data))

    _spawned(monkeypatch, tmp_path)

    seeded = tmp_path / "home" / ".local" / "share" / "opencode" / "auth.json"
    assert seeded.read_text() == '{"openai": {"type": "oauth"}}'


@pytest.mark.parametrize("customizations", [False, True])
def test_a_jsonc_only_config_preserves_the_users_connection(
    monkeypatch, tmp_path, customizations
) -> None:
    real = Path.home() / ".config" / "opencode"
    real.mkdir(parents=True)
    (real / "opencode.jsonc").write_text(
        '{"model": "local/test", "provider": {"local": {"options": '
        '{"baseURL": "http://localhost:1234/v1"}}}, "instructions": ["rules.md"],}'
    )

    _, result = _spawned(monkeypatch, tmp_path, user_customizations=customizations)

    config = OPENCODE_CONFIG.read(tmp_path / "home" / ".config" / "opencode")
    expected = {
        "model": "local/test",
        "provider": {"local": {"options": {"baseURL": "http://localhost:1234/v1"}}},
    }
    if customizations:
        expected["instructions"] = ["rules.md"]
    assert config == expected
    assert result.loaded_user_customizations == (
        ["settings:opencode.jsonc"] if customizations else None
    )


@pytest.mark.parametrize("customizations", [False, True])
def test_global_config_files_merge_in_opencodes_order(
    monkeypatch, tmp_path, customizations
) -> None:
    real = Path.home() / ".config" / "opencode"
    real.mkdir(parents=True)
    (real / "config.json").write_text(
        json.dumps(
            {
                "model": "local/old",
                "enabled_providers": ["old"],
                "provider": {
                    "local": {"name": "Local", "options": {"baseURL": "http://old"}}
                },
                "mcp": {"mine": {"type": "local", "command": ["mine"]}},
            }
        )
    )
    (real / "opencode.json").write_text(
        json.dumps(
            {
                "model": "local/json",
                "provider": {"local": {"options": {"apiKey": "test-key"}}},
                "instructions": ["old.md"],
            }
        )
    )
    (real / "opencode.jsonc").write_text(
        json.dumps(
            {
                "model": "local/jsonc",
                "enabled_providers": ["local"],
                "provider": {"local": {"options": {"baseURL": "http://new"}}},
                "instructions": ["new.md"],
            }
        )
    )

    _, result = _spawned(monkeypatch, tmp_path, user_customizations=customizations)

    config = OPENCODE_CONFIG.read(tmp_path / "home" / ".config" / "opencode")
    expected = {
        "model": "local/jsonc",
        "enabled_providers": ["local"],
        "provider": {
            "local": {
                "name": "Local",
                "options": {"baseURL": "http://new", "apiKey": "test-key"},
            }
        },
    }
    if customizations:
        expected["instructions"] = ["new.md"]
        expected["mcp"] = {"mine": {"type": "local", "command": ["mine"]}}
    assert config == expected
    assert result.loaded_user_customizations == (
        [
            "mcp:mine",
            "settings:config.json",
            "settings:opencode.json",
            "settings:opencode.jsonc",
        ]
        if customizations
        else None
    )


@pytest.mark.parametrize("normal_type", ["api", "oauth"])
def test_an_isolated_attempt_refuses_wellknown_remote_settings(
    monkeypatch, tmp_path, normal_type
) -> None:
    data = tmp_path / "user-data"
    real = data / "opencode"
    real.mkdir(parents=True)
    monkeypatch.setenv("XDG_DATA_HOME", str(data))
    (real / "auth.json").write_text(
        json.dumps(
            {
                "provider": {"type": normal_type, "key": "normal-secret"},
                "https://private.example": {
                    "type": "wellknown",
                    "key": "PRIVATE_KEY",
                    "token": "private-token",
                },
            }
        )
    )

    with pytest.raises(HarnessConfigurationError) as raised:
        _spawned(monkeypatch, tmp_path)

    message = str(raised.value)
    assert "remote settings" in message
    assert "--user-customizations" in message
    assert "API/OAuth" in message
    for private in (
        "https://private.example",
        "PRIVATE_KEY",
        "private-token",
        "normal-secret",
    ):
        assert private not in message


@pytest.mark.parametrize("state", ["active", "ablated", "direct"])
def test_wellknown_settings_refuse_declared_mcp_even_with_user_customizations(
    monkeypatch, tmp_path, state
) -> None:
    data = tmp_path / "user-data"
    real = data / "opencode"
    real.mkdir(parents=True)
    monkeypatch.setenv("XDG_DATA_HOME", str(data))
    (real / "auth.json").write_text(
        '{"https://private.example": {"type": "wellknown", "key": "KEY", "token": "secret"}}'
    )
    servers = {} if state == "ablated" else {"echo": McpServer(command="echo")}
    names = frozenset() if state == "direct" else frozenset({"echo"})

    with pytest.raises(
        HarnessConfigurationError, match="declared or ablated"
    ) as raised:
        _spawned(
            monkeypatch,
            tmp_path,
            user_customizations=True,
            mcp_servers=servers,
            spec_mcp_names=names,
        )

    assert "API/OAuth" in str(raised.value)
    assert "private.example" not in str(raised.value)
    assert "secret" not in str(raised.value)


def test_remote_settings_are_allowed_but_unlisted_with_user_customizations(
    monkeypatch, tmp_path
) -> None:
    data = tmp_path / "user-data"
    real = data / "opencode"
    real.mkdir(parents=True)
    monkeypatch.setenv("XDG_DATA_HOME", str(data))
    auth = '{"https://private.example": {"type": "wellknown", "key": "KEY", "token": "token"}}'
    (real / "auth.json").write_text(auth)

    _, result = _spawned(monkeypatch, tmp_path, user_customizations=True)

    assert result.loaded_user_customizations == ["mcp:(not listed)"]
    seeded = tmp_path / "home" / ".local" / "share" / "opencode" / "auth.json"
    assert seeded.read_text() == auth


@pytest.mark.parametrize("normal_type", ["api", "oauth"])
def test_normal_provider_logins_remain_available_when_isolated(
    monkeypatch, tmp_path, normal_type
) -> None:
    data = tmp_path / "user-data"
    real = data / "opencode"
    real.mkdir(parents=True)
    monkeypatch.setenv("XDG_DATA_HOME", str(data))
    auth = json.dumps({"provider": {"type": normal_type, "key": "normal-secret"}})
    (real / "auth.json").write_text(auth)

    _, result = _spawned(monkeypatch, tmp_path)

    assert result.loaded_user_customizations is None
    seeded = tmp_path / "home" / ".local" / "share" / "opencode" / "auth.json"
    assert seeded.read_text() == auth


def test_copied_user_directories_are_recorded_without_a_json_config(
    monkeypatch, tmp_path
) -> None:
    config_root = tmp_path / "user-config"
    real = config_root / "opencode"
    (real / "plugins").mkdir(parents=True)
    (real / "agents").mkdir()
    (real / "tools").mkdir()
    (real / "modes").mkdir()
    (real / "plugins" / "probe.js").write_text("export default async () => ({})")
    (real / "agents" / "review.md").write_text("Review the code.")
    # Hidden Finder metadata and empty directories are ignored by OpenCode's scanners.
    (real / "tools" / ".DS_Store").write_bytes(b"\x00\x00")
    (real / "modes" / "empty-subdir").mkdir()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_root))

    _, result = _spawned(monkeypatch, tmp_path, user_customizations=True)

    assert result.loaded_user_customizations == ["plugin:plugins/", "settings:agents/"]
    copied = tmp_path / "home" / ".config" / "opencode"
    assert (
        copied / "plugins" / "probe.js"
    ).read_text() == "export default async () => ({})"
    assert (copied / "agents" / "review.md").read_text() == "Review the code."


def test_singular_skill_directory_and_bom_wellknown_auth_are_handled(
    monkeypatch, tmp_path
) -> None:
    config_root = tmp_path / "user-config"
    singular = config_root / "opencode" / "skill" / "solo"
    singular.mkdir(parents=True)
    (singular / "SKILL.md").write_text(
        "---\nname: solo\ndescription: singular root\n---\nUse solo."
    )
    data = tmp_path / "user-data"
    (data / "opencode").mkdir(parents=True)
    (data / "opencode" / "auth.json").write_bytes(
        b'\xef\xbb\xbf{"https://private.example": {"type": "wellknown", "key": "K", "token": "T"}}'
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_root))
    monkeypatch.setenv("XDG_DATA_HOME", str(data))

    with pytest.raises(HarnessConfigurationError, match="remote settings"):
        _spawned(monkeypatch, tmp_path)

    _, result = _spawned(monkeypatch, tmp_path, user_customizations=True)
    assert result.loaded_user_customizations == ["mcp:(not listed)", "skill:solo"]


def test_empty_bom_and_v2_opencode_configs_normalize_across_files(
    monkeypatch, tmp_path
) -> None:
    config_root = tmp_path / "user-config"
    real = config_root / "opencode"
    real.mkdir(parents=True)
    # 0-byte config.json, UTF-8 BOM + V2 shorthands in opencode.json, override in jsonc.
    (real / "config.json").write_text("")
    (real / "opencode.json").write_bytes(
        b'\xef\xbb\xbf{"permission": "allow", '
        b'"model": {"providerID": "local", "model": "v2"}, '
        b'"mcp": {"servers": {"echo": {"type": "local", "command": ["user-echo"]}, '
        b'"keep": {"type": "local", "command": ["keep-cmd"]}}}}'
    )
    (real / "opencode.jsonc").write_text('{"permission": {"bash": "deny"}}')
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_root))

    _, result = _spawned(
        monkeypatch,
        tmp_path,
        user_customizations=True,
        mcp_servers={},
        spec_mcp_names=frozenset({"echo"}),
    )

    staged = OPENCODE_CONFIG.read(tmp_path / "home" / ".config" / "opencode")
    assert staged["model"] == "local/v2"
    assert staged["permission"] == {"*": "allow", "bash": "deny"}
    assert set(staged["mcp"]) == {"keep"}
    assert "mcp:keep" in result.loaded_user_customizations
    assert "mcp:echo" not in result.loaded_user_customizations


@pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")
def test_user_directory_links_survive_staging_when_dangling_or_escaping(
    monkeypatch, tmp_path
) -> None:
    config_root = tmp_path / "user-config"
    shared = tmp_path / "shared"
    shared.mkdir()
    (shared / "review.md").write_text("Shared reviewer.")
    agents = config_root / "opencode" / "agents"
    agents.mkdir(parents=True)
    (agents / "review.md").symlink_to("../../../shared/review.md")
    (agents / "dangling.md").symlink_to("missing.md")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_root))

    _, result = _spawned(monkeypatch, tmp_path, user_customizations=True)

    copied = tmp_path / "home" / ".config" / "opencode" / "agents"
    assert (copied / "review.md").read_text() == "Shared reviewer."
    assert (copied / "dangling.md").is_symlink()
    assert result.loaded_user_customizations == ["settings:agents/"]


def test_v1_server_named_servers_and_v2_aliases_merge_cleanly(
    monkeypatch, tmp_path
) -> None:
    config_root = tmp_path / "xdg-config"
    real = config_root / "opencode"
    real.mkdir(parents=True)
    (real / "config.json").write_text(
        json.dumps(
            {
                "mcp": {
                    "servers": {"type": "local", "command": ["v1-named-servers"]},
                },
                "skills": ["/dir-one", "https://example.com/skill"],
                "agents": {
                    "reviewer": {
                        "system": "Review carefully.",
                        "model": {"providerID": "openai", "model": "gpt-6.1-terra"},
                    }
                },
                "commands": {
                    "ship": {
                        "template": "Ship it.",
                        "model": {"providerID": "openai", "model": "gpt-6.1-mini"},
                    }
                },
            }
        )
    )
    (real / "opencode.jsonc").write_text(
        json.dumps(
            {
                "mcp": {
                    "timeout": {"catalog": 5000, "execution": 5000},
                    "servers": {"extra": {"type": "local", "command": ["v2-extra"]}},
                },
                "skills": {"paths": ["/dir-two"]},
                "agent": {"reviewer": {"description": "Code reviewer"}},
            }
        )
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_root))

    _, result = _spawned(monkeypatch, tmp_path, user_customizations=True)

    staged = json.loads(
        (tmp_path / "home" / ".config" / "opencode" / "opencode.json").read_text()
    )
    assert staged["mcp"] == {
        "servers": {"type": "local", "command": ["v1-named-servers"]},
        "extra": {"type": "local", "command": ["v2-extra"]},
    }
    assert staged["experimental"]["mcp_timeout"] == 5000
    assert staged["skills"] == {
        "paths": ["/dir-two"],
        "urls": ["https://example.com/skill"],
    }
    assert "agents" not in staged
    assert staged["agent"]["reviewer"] == {
        "prompt": "Review carefully.",
        "model": "openai/gpt-6.1-terra",
        "description": "Code reviewer",
    }
    assert "commands" not in staged
    assert staged["command"]["ship"]["model"] == "openai/gpt-6.1-mini"
    assert "mcp:servers" in (result.loaded_user_customizations or [])
    assert "mcp:extra" in (result.loaded_user_customizations or [])
    assert "mcp:timeout" not in (result.loaded_user_customizations or [])


def test_a_rejected_key_is_a_login_refusal(monkeypatch, tmp_path) -> None:
    failed = {
        "type": "error",
        "error": {
            "name": "APIError",
            "data": {"message": "invalid x-api-key", "statusCode": 401},
        },
    }

    with pytest.raises(LoginRequired) as raised:
        _spawned(monkeypatch, tmp_path, stdout=_stdout([failed]))

    assert "APIError (401): invalid x-api-key" in str(raised.value)
    assert raised.value.command == ["opencode", "auth", "login"]


def test_a_throttle_in_an_error_event_is_retried_not_refused() -> None:
    harness = OpenCodeHarness()
    throttled = {
        "type": "error",
        "error": {
            "name": "APIError",
            "data": {"message": "slow down", "statusCode": 429},
        },
    }
    proc = ProcessResult(_stdout([throttled]), "", 1, False)

    refusal = harness._refusal(proc, harness._read(proc, run_context()))

    assert refusal is not None
    assert refusal.kind is RefusalKind.THROTTLE


def test_an_unknown_model_stops_the_run_with_how_to_list_models(
    monkeypatch, tmp_path
) -> None:
    """opencode's stream says only "Unexpected server error"; its log names the model."""
    stream = {
        "type": "error",
        "error": {
            "name": "UnknownError",
            "data": {"message": "Unexpected server error. Check server logs."},
        },
    }
    log = (
        "timestamp=2026-11-15T02:39:12.413Z level=ERROR message=failed "
        'error="ProviderModelNotFoundError: Model not found: opencode/bogus."'
    )

    def fake_run(cmd, **kwargs):
        if cmd[1:] == ["--version"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="1.19.5\n", stderr="")
        return subprocess.CompletedProcess(cmd, 1, stdout=_stdout([stream]), stderr=log)

    monkeypatch.setattr("caliper.harness.base.shutil.which", lambda _n, **_: "opencode")
    patch_cli_calls(monkeypatch, fake_run)

    with pytest.raises(HarnessConfigurationError) as raised:
        OpenCodeHarness(model="opencode/bogus").run(
            run_context(isolated_home=str(tmp_path / "home"), workdir=str(tmp_path))
        )

    message = str(raised.value)
    assert "Model not found: opencode/bogus." in message
    assert "opencode models" in message
    assert "--model opencode:<provider>/<model>" in message


def test_the_judge_runs_without_auto_approval_in_the_real_env(
    monkeypatch, tmp_path
) -> None:
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return subprocess.CompletedProcess(cmd, 0, stdout=_stdout(_STREAM), stderr="")

    monkeypatch.setattr("caliper.harness.base.shutil.which", lambda _n, **_: "opencode")
    patch_cli_calls(monkeypatch, fake_run)

    result = OpenCodeHarness().run_prompt("Grade this", model="a/b", cwd=str(tmp_path))

    cmd, kwargs = calls[-1]
    assert "--auto" not in cmd
    assert cmd[cmd.index("--model") + 1] == "a/b"
    assert kwargs["input"] == "Grade this"
    assert kwargs["env"]["HOME"] == str(Path.home())
    assert kwargs["env"]["OPENCODE_DISABLE_AUTOUPDATE"] == "1"
    assert result.text == "hello caliper"


def test_an_xdg_home_that_is_not_a_directory_stops_the_run(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "missing"))

    with pytest.raises(HarnessConfigurationError, match="XDG_CONFIG_HOME"):
        OpenCodeHarness()._ensure_ready(run_context(isolated_home=str(tmp_path)))


# --- planted history ---------------------------------------------------------


def test_a_planted_history_is_imported_then_resumed(monkeypatch, tmp_path) -> None:
    history = [PlantedTurn(user="hi"), PlantedTurn(assistant="hello")]

    calls, _ = _spawned(
        monkeypatch, tmp_path, prompt="Finish it.", history=history, model="local/test"
    )

    (imported, how), (resumed, _) = [
        call for call in calls if call[0][1:] != ["--version"]
    ]
    assert imported[1] == "import"
    session = json.loads(Path(imported[2]).read_text())
    assert resumed[resumed.index("--session") + 1] == session["info"]["id"]
    # Imported into the attempt's own database, never the operator's.
    assert how["env"]["HOME"] == str(tmp_path / "home")


def test_a_failed_import_stops_the_run(monkeypatch, tmp_path) -> None:
    def fake_run(cmd, **kwargs):
        if cmd[1:] == ["--version"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="1.19.5\n", stderr="")
        if cmd[1] == "import":
            return subprocess.CompletedProcess(
                cmd, 1, stdout="", stderr="\x1b[91mError: \x1b[0mMissing key"
            )
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr("caliper.harness.base.shutil.which", lambda _n, **_: "opencode")
    patch_cli_calls(monkeypatch, fake_run)
    (tmp_path / "work").mkdir()

    with pytest.raises(HarnessConfigurationError) as raised:
        OpenCodeHarness().run(
            run_context(
                isolated_home=str(tmp_path / "home"),
                workdir=str(tmp_path / "work"),
                history=[PlantedTurn(user="hi")],
                model="local/test",
            )
        )

    assert "Error: Missing key" in str(raised.value)


def test_no_history_imports_nothing(monkeypatch, tmp_path) -> None:
    calls, _ = _spawned(monkeypatch, tmp_path)

    assert not any(cmd[1] == "import" for cmd, _ in calls)
    assert "--session" not in calls[-1][0]


def test_a_planted_session_is_one_export_document(monkeypatch, tmp_path) -> None:
    history = [
        PlantedTurn(user="What's in vault.txt?"),
        PlantedTurn(
            assistant="Reading it.",
            shell=[PlantedShellCall(run="cat vault.txt", output="PURPLE-OTTER-42")],
        ),
    ]
    calls, _ = _spawned(monkeypatch, tmp_path, history=history, model="local/test")
    imported = next(cmd for cmd, _ in calls if cmd[1:2] == ["import"])

    session = json.loads(Path(imported[2]).read_text())

    info = session["info"]
    # opencode's provider refused a session that named no version (1.19.5).
    assert info["version"] == "1.19.5"
    assert info["directory"] == str((tmp_path / "work").resolve())
    user, assistant = session["messages"]
    assert user["info"]["role"] == "user"
    assert assistant["info"]["parentID"] == user["info"]["id"]
    # Message ids ascend, so the attempt's own turns sort after the planted ones.
    assert user["info"]["id"] < assistant["info"]["id"]
    assert info["id"].startswith("ses_")
    text, tool = assistant["parts"]
    assert text == {**text, "type": "text", "text": "Reading it."}
    assert tool["tool"] == "bash"
    assert tool["state"]["input"]["command"] == "cat vault.txt"
    assert tool["state"]["output"] == "PURPLE-OTTER-42"
    assert {part["messageID"] for part in assistant["parts"]} == {
        assistant["info"]["id"]
    }
