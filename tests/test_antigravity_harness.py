from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import patch_cli_calls, run_context

from caliper.backends import mcp_server_of, normalize_backend
from caliper.harness import get_harness
from caliper.harness.antigravity import AntigravityHarness
from caliper.harness.base import HarnessConfigurationError, LoginRequired
from caliper.schema.spec import McpServer
from caliper.skills import resolve_skills

# A real agy 1.5.9 stream: the agent reads an installed skill, runs the shell
# command it names, then answers (tool list trimmed, paths scrubbed).
_SKILL_AND_SHELL = (
    Path(__file__).parent / "fixtures" / "antigravity" / "skill_and_shell.jsonl"
)

# What agy 1.5.9 writes when it has no login and no terminal to sign in from.
_AUTH_REQUIRED_STDERR = (
    "Authentication required. Please visit the URL to log in:\n"
    "  https://accounts.google.com/o/oauth2/auth?client_id=x\n\n"
    "Waiting for authentication (timeout 60s)...\n"
)

_UNKNOWN_MODEL = {
    "event": "result",
    "result": {
        "conversation_id": "",
        "status": "ERROR",
        "response": "",
        "error": (
            'invalid model selection (--model "nope" --effort ""): model nope '
            "is not recognized as a known model or custom model in settings\n"
            "Available models:\n  Gemini 3.8 Flash (Medium)"
        ),
        "num_turns": 0,
        "finished_reason": "error",
    },
}


def _version(cmd):
    return subprocess.CompletedProcess(cmd, 0, stdout="1.5.9\n", stderr="")


def _fake_agy(monkeypatch, *, stdout="", stderr="", returncode=0) -> list:
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        if cmd[1:] == ["--version"]:
            return _version(cmd)
        return subprocess.CompletedProcess(
            cmd, returncode, stdout=stdout, stderr=stderr
        )

    monkeypatch.setattr("caliper.harness.base.shutil.which", lambda _n, **_: "agy")
    patch_cli_calls(monkeypatch, fake_run)
    return calls


def _run(tmp_path, **overrides):
    fields = {"isolated_home": str(tmp_path), "workdir": str(tmp_path / "work")}
    return AntigravityHarness().run(run_context(**{**fields, **overrides}))


def test_antigravity_is_registered_with_an_agy_alias() -> None:
    assert normalize_backend("agy") == "antigravity"
    assert isinstance(get_harness("agy", model="m"), AntigravityHarness)


def test_antigravity_sends_the_prompt_on_stdin_and_installs_the_skill(
    monkeypatch, tmp_path
) -> None:
    skill_dir = tmp_path / "src"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: careful\ndescription: test\n---\n\nUse caliper carefully."
    )
    refs = resolve_skills([str(skill_dir / "SKILL.md")], tmp_path)
    calls = _fake_agy(monkeypatch)

    # A flag-shaped prompt: agy refuses one as --print's value.
    _run(tmp_path, prompt="--version", skill_refs=refs, model="gemini-3.8-flash")

    cmd, kwargs = calls[1]
    assert cmd[0] == "agy"
    assert "--print" not in cmd
    assert cmd[cmd.index("--input-format") + 1] == "stream-json"
    assert cmd[cmd.index("--output-format") + 1] == "stream-json"
    assert cmd[cmd.index("--model") + 1] == "gemini-3.8-flash"
    assert "--dangerously-skip-permissions" in cmd
    assert "--version" not in cmd
    message = json.loads(kwargs["input"])
    assert message == {"event": "user", "message": {"content": "--version"}}
    # Discovered, never preloaded (docs/adr/0013).
    installed = tmp_path / ".gemini" / "antigravity-cli" / "skills" / "careful"
    assert (installed / "SKILL.md").exists()
    env = kwargs["env"]
    assert env["HOME"] == str(tmp_path)
    assert env["AGY_CLI_DISABLE_AUTO_UPDATE"] == "1"


def test_antigravity_omits_model_when_unspecified(monkeypatch, tmp_path) -> None:
    calls = _fake_agy(monkeypatch)

    _run(tmp_path)

    assert "--model" not in calls[1][0]


def test_antigravity_seeds_the_cli_settings(monkeypatch, tmp_path) -> None:
    real_home = tmp_path / "real"
    settings = real_home / ".gemini" / "antigravity-cli" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text('{"modelProvider": "gemini"}')
    monkeypatch.setattr(Path, "home", lambda: real_home)
    _fake_agy(monkeypatch)

    attempt_home = tmp_path / "attempt"
    _run(attempt_home)

    seeded = attempt_home / ".gemini" / "antigravity-cli" / "settings.json"
    assert seeded.read_text() == '{"modelProvider": "gemini"}'


@pytest.mark.skipif(sys.platform != "darwin", reason="the macOS keychain")
def test_antigravity_links_the_login_keychain_into_the_attempt_home(
    monkeypatch, tmp_path
) -> None:
    real_home = tmp_path / "real"
    (real_home / "Library" / "Keychains").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", lambda: real_home)
    _fake_agy(monkeypatch)

    attempt_home = tmp_path / "attempt"
    _run(attempt_home)

    link = attempt_home / "Library" / "Keychains"
    assert link.is_symlink()
    assert link.resolve() == (real_home / "Library" / "Keychains").resolve()
    # Invoked again into the same home (docs/adr/0019): the link is kept.
    _run(attempt_home)
    assert link.is_symlink()


def test_antigravity_does_not_grade_an_unparsed_stream(monkeypatch, tmp_path) -> None:
    _fake_agy(monkeypatch, stdout="not an event\n")

    result = _run(tmp_path)

    assert result.transcript == []
    assert result.final_output == ""


def test_antigravity_rejected_option_names_the_cli(monkeypatch, tmp_path) -> None:
    _fake_agy(
        monkeypatch,
        stderr="flags provided but not defined: -bogus\nUsage of agy:\n",
        returncode=2,
    )

    with pytest.raises(HarnessConfigurationError, match="does not accept an option"):
        _run(tmp_path)


def test_antigravity_reads_tool_calls_answer_and_usage(monkeypatch, tmp_path) -> None:
    _fake_agy(monkeypatch, stdout=_SKILL_AND_SHELL.read_text())

    result = _run(tmp_path)

    assert result.final_output == "The secret word is **PAMPLEMOUSSE**."
    assert [turn.role for turn in result.transcript] == [
        "tool_use",
        "tool_result",
        "tool_use",
        "tool_result",
        "assistant",
    ]
    read, _, shell, ran, _ = result.transcript
    assert read.tool_name == "read_file"
    # The path is what activation matches a skill read on.
    assert read.tool_input["Path"].endswith("secret-word/SKILL.md")
    assert shell.tool_input == {"CommandLine": "echo checked > proof.txt"}
    assert "Command exited with code 0." in ran.tool_output
    # Summed over the finished steps; agy's input is already non-cached.
    assert result.usage.input_tokens == 14079
    assert result.usage.output_tokens == 355
    assert result.usage.cache_read_tokens == 15099
    assert result.usage.total_tokens == 14079 + 355 + 15099
    assert not result.salvaged


def test_antigravity_joins_streamed_text_deltas_into_one_turn(
    monkeypatch, tmp_path
) -> None:
    def step(state, delta):
        return {
            "event": "step_update",
            "step_update": {
                "step_index": 1,
                "state": state,
                "step_type": "agent_response",
                "text_delta": delta,
            },
        }

    events = [step("ACTIVE", "Hel"), step("ACTIVE", "lo"), step("DONE", "\n")]
    _fake_agy(monkeypatch, stdout="\n".join(json.dumps(e) for e in events))

    result = _run(tmp_path)

    assert [turn.content for turn in result.transcript] == ["Hello"]
    # No result event (a timeout cuts it off): the last turn is the answer.
    assert result.final_output == "Hello"


def test_antigravity_missing_cli_raises_configuration_error(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setattr("caliper.harness.base.shutil.which", lambda _n: None)
    monkeypatch.setattr(AntigravityHarness, "cli_candidates", lambda self: ())

    with pytest.raises(HarnessConfigurationError, match="Antigravity CLI is not"):
        _run(tmp_path)


def test_antigravity_without_a_login_asks_to_sign_in(monkeypatch, tmp_path) -> None:
    _fake_agy(monkeypatch, stderr=_AUTH_REQUIRED_STDERR, returncode=1)

    with pytest.raises(LoginRequired, match="Authentication required") as exc:
        _run(tmp_path)

    assert exc.value.command == ["agy"]


def test_antigravity_unknown_model_stops_the_run(monkeypatch, tmp_path) -> None:
    _fake_agy(monkeypatch, stdout=json.dumps(_UNKNOWN_MODEL), returncode=1)

    with pytest.raises(HarnessConfigurationError) as exc:
        _run(tmp_path, model="nope")

    assert type(exc.value) is HarnessConfigurationError
    message = str(exc.value)
    assert message.startswith("Antigravity CLI cannot run the requested model")
    assert "invalid model selection" in message
    assert "`agy models`" in message
    assert "`--model antigravity:<model>`" in message


def test_antigravity_judge_reads_the_result_answer(monkeypatch, tmp_path) -> None:
    result_event = {
        "event": "result",
        "result": {"status": "SUCCESS", "response": '{"pass": true}\n'},
    }
    calls = _fake_agy(monkeypatch, stdout=json.dumps(result_event))

    result = AntigravityHarness().run_prompt("--grade this", cwd=str(tmp_path))

    assert result.text == '{"pass": true}'
    assert result.error is None
    cmd, kwargs = calls[-1]
    assert "--dangerously-skip-permissions" not in cmd
    assert json.loads(kwargs["input"])["message"]["content"] == "--grade this"


def test_antigravity_judge_on_an_unknown_model_names_the_judge_flag(
    monkeypatch, tmp_path
) -> None:
    _fake_agy(monkeypatch, stdout=json.dumps(_UNKNOWN_MODEL), returncode=1)

    with pytest.raises(HarnessConfigurationError) as exc:
        AntigravityHarness(model="nope").run_prompt("anything", cwd=str(tmp_path))

    message = str(exc.value)
    assert message.startswith("The antigravity judge cannot run.")
    assert "`--judge-model antigravity:<model>`" in message


def test_antigravity_writes_declared_mcp_servers_into_the_attempt_config(
    monkeypatch, tmp_path
) -> None:
    real_home = tmp_path / "real"
    user_config = real_home / ".gemini" / "config" / "mcp_config.json"
    user_config.parent.mkdir(parents=True)
    # agy's installer leaves an empty file behind; it reads as no servers.
    user_config.write_text("")
    monkeypatch.setattr(Path, "home", lambda: real_home)
    monkeypatch.setenv("MCP_TOKEN", "s3cret")
    monkeypatch.setattr(
        "caliper.harness.mcp.preflight_stdio_servers", lambda *a, **kw: None
    )
    _fake_agy(monkeypatch)

    attempt_home = tmp_path / "attempt"
    result = _run(
        attempt_home,
        mcp_servers={
            "echo": McpServer(command="python3", args=["echo.py"]),
            "remote": McpServer(
                type="http",
                url="https://x/mcp",
                headers={"Authorization": "Bearer ${MCP_TOKEN}"},
            ),
        },
        spec_mcp_names=frozenset({"echo", "remote"}),
    )

    written = attempt_home / ".gemini" / "config" / "mcp_config.json"
    assert json.loads(written.read_text()) == {
        "mcpServers": {
            "echo": {"command": "python3", "args": ["echo.py"]},
            "remote": {
                "url": "https://x/mcp",
                "headers": {"Authorization": "Bearer s3cret"},
            },
        }
    }
    if os.name == "posix":
        assert stat.S_IMODE(written.stat().st_mode) == 0o600
    # The user's real config is never touched.
    assert user_config.read_text() == ""
    assert result.loaded_user_customizations is None


def test_antigravity_names_a_lazy_mcp_call_by_its_server_and_tool(
    monkeypatch, tmp_path
) -> None:
    parameters = {"Arguments": {"q": 1}, "ServerName": "echo", "ToolName": "word"}
    step = {
        "event": "step_update",
        "step_update": {
            "step_index": 4,
            "state": "DONE",
            "step_type": "tool",
            "tool_name": "call_mcp_tool",
            "tool_info": {
                "name": "call_mcp_tool",
                "parameters": parameters,
                "output": "caliper",
            },
        },
    }
    _fake_agy(monkeypatch, stdout=json.dumps(step))

    result = _run(tmp_path)

    call, answer = result.transcript
    assert call.tool_name == "mcp_echo_word"
    assert call.tool_input == {"q": 1}
    assert answer.tool_output == "caliper"
    assert mcp_server_of(call.tool_name, ["echo"], "antigravity") == "echo"


def test_antigravity_loads_user_rules_and_every_user_skill_dir(
    monkeypatch, tmp_path
) -> None:
    real_home = tmp_path / "real"
    gemini = real_home / ".gemini"
    for root, name in [
        (gemini / "antigravity-cli" / "skills", "cli-skill"),
        (gemini / "skills", "shared-skill"),
        (gemini / "config" / "skills", "app-skill"),
        # A skill the CLI's own dir already names: that one wins.
        (gemini / "skills", "cli-skill"),
    ]:
        skill = root / name
        skill.mkdir(parents=True, exist_ok=True)
        if not (skill / "SKILL.md").exists():
            (skill / "SKILL.md").write_text(
                f"---\nname: {name}\ndescription: test\n---\n{root.name}"
            )
    (gemini / "GEMINI.md").write_text("rules")
    (gemini / "config").mkdir(exist_ok=True)
    (gemini / "config" / "AGENTS.md").write_text("more rules")
    (gemini / "config" / "mcp_config.json").write_text(
        '{"mcpServers": {"mine": {"command": "mine"}}}'
    )
    monkeypatch.setattr(Path, "home", lambda: real_home)
    _fake_agy(monkeypatch)

    attempt_home = tmp_path / "attempt"
    result = _run(attempt_home, user_customizations=True)

    attempt_gemini = attempt_home / ".gemini"
    installed = attempt_gemini / "antigravity-cli" / "skills"
    assert sorted(p.name for p in installed.iterdir()) == [
        "app-skill",
        "cli-skill",
        "shared-skill",
    ]
    assert (installed / "cli-skill" / "SKILL.md").read_text().endswith("skills")
    assert (attempt_gemini / "GEMINI.md").read_text() == "rules"
    assert (attempt_gemini / "config" / "AGENTS.md").read_text() == "more rules"
    assert result.loaded_user_customizations == [
        "mcp:mine",
        "rules:GEMINI.md",
        "rules:config/AGENTS.md",
        "skill:app-skill",
        "skill:cli-skill",
        "skill:shared-skill",
    ]


def test_antigravity_refuses_planted_history_by_design() -> None:
    harness = AntigravityHarness()

    assert harness.supports_mcp
    assert not harness.supports_history
    assert "SQLite" in harness.history_unsupported_hint
