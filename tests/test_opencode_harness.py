from __future__ import annotations

import json
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
    assert (home / "opencode.json").stat().st_mode & 0o777 == 0o600


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
          "mcp": {"mine": {"type": "local", "command": ["mine"]}},
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

    calls, _ = _spawned(monkeypatch, tmp_path, prompt="Finish it.", history=history)

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
    calls, _ = _spawned(monkeypatch, tmp_path, history=history)
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
