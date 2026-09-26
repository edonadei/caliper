from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from caliper.harness.base import HarnessConfigurationError
from caliper.harness.pi import PiHarness
from caliper.harness.refusal import RefusalKind
from caliper.skills import resolve_skills

from conftest import patch_cli_calls, run_context


def _version(cmd):
    return subprocess.CompletedProcess(cmd, 0, stdout="0.80.2\n", stderr="")


def test_pi_installs_the_skill_and_passes_no_preload_flag(
    monkeypatch, tmp_path
) -> None:
    skill_dir = tmp_path / "src"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: careful\ndescription: test\n---\n\nUse caliper carefully."
    )
    refs = resolve_skills([str(skill_dir / "SKILL.md")], tmp_path)
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        if cmd[1:] == ["--version"]:
            return _version(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr("caliper.harness.base.shutil.which", lambda _n: "pi")
    patch_cli_calls(monkeypatch, fake_run)

    PiHarness().run(
        run_context(
            prompt="Validate the spec",
            skill_refs=refs,
            model="claude-sonnet-4-6",
            timeout=30,
            isolated_home=str(tmp_path),
            extra_path=[str(tmp_path / "bin")],
        )
    )

    run_cmd = calls[1][0]
    assert run_cmd[0] == "pi"
    assert "--print" in run_cmd
    assert run_cmd[run_cmd.index("--mode") + 1] == "json"
    assert "--no-session" in run_cmd
    assert "--approve" in run_cmd
    assert calls[1][1]["stdin"] is subprocess.DEVNULL
    assert run_cmd[run_cmd.index("--model") + 1] == "claude-sonnet-4-6"
    # --skill preloads; pi's own --no-skills exists because discovery is its
    # default, so the skill is installed under the agent dir and left to be found.
    assert "--skill" not in run_cmd
    assert (tmp_path / ".pi" / "agent" / "skills" / "careful" / "SKILL.md").exists()
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

    monkeypatch.setattr("caliper.harness.base.shutil.which", lambda _n: "pi")
    patch_cli_calls(monkeypatch, fake_run)

    PiHarness().run(
        run_context(
            prompt="Hello",
            model=None,
            timeout=12,
            isolated_home=str(tmp_path),
        )
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

    monkeypatch.setattr("caliper.harness.base.shutil.which", lambda _n: "pi")
    patch_cli_calls(monkeypatch, fake_run)

    result = PiHarness().run(
        run_context(
            prompt="Write a file",
            model=None,
            timeout=12,
            isolated_home=str(tmp_path),
        )
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

    monkeypatch.setattr("caliper.harness.base.shutil.which", lambda _n: "pi")
    patch_cli_calls(monkeypatch, fake_run)

    result = PiHarness().run(
        run_context(
            prompt="Write hello to a file",
            model=None,
            timeout=12,
            isolated_home=str(tmp_path),
        )
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
    monkeypatch.setattr("caliper.harness.base.shutil.which", lambda _n: None)
    monkeypatch.delenv("PI_CLI_PATH", raising=False)

    with pytest.raises(HarnessConfigurationError, match="pi CLI is not available"):
        PiHarness().run(
            run_context(
                prompt="Hello",
                model=None,
                timeout=12,
                isolated_home=str(tmp_path),
            )
        )


def test_pi_auth_failure_raises_configuration_error(monkeypatch, tmp_path) -> None:
    def fake_run(cmd, **kwargs):
        if cmd[1:] == ["--version"]:
            return _version(cmd)
        return subprocess.CompletedProcess(
            cmd, 1, stdout="", stderr="Error: not authenticated. Please run /login."
        )

    monkeypatch.setattr("caliper.harness.base.shutil.which", lambda _n: "pi")
    patch_cli_calls(monkeypatch, fake_run)

    with pytest.raises(HarnessConfigurationError, match="authentication"):
        PiHarness().run(
            run_context(
                prompt="Hello",
                model=None,
                timeout=12,
                isolated_home=str(tmp_path),
            )
        )


# A real pi 0.87.1 stream from an expired OAuth login, recorded with a bogus
# refresh token (system prompt trimmed, paths scrubbed). pi exits 0 and reports
# the failure only as an errored assistant message inside the stream.
_OAUTH_EXPIRED = (
    Path(__file__).parent / "fixtures" / "pi" / "oauth_refresh_expired.jsonl"
)


def _run_with_stream(monkeypatch, tmp_path, stdout: str):
    def fake_run(cmd, **kwargs):
        if cmd[1:] == ["--version"]:
            return _version(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

    monkeypatch.setattr("caliper.harness.base.shutil.which", lambda _n: "pi")
    patch_cli_calls(monkeypatch, fake_run)
    return PiHarness().run(
        run_context(
            prompt="Hello",
            model="claude-haiku-4-5",
            timeout=12,
            isolated_home=str(tmp_path),
        )
    )


def test_pi_expired_oauth_on_a_zero_exit_raises_configuration_error(
    monkeypatch, tmp_path
) -> None:
    with pytest.raises(HarnessConfigurationError, match="/login") as exc:
        _run_with_stream(monkeypatch, tmp_path, _OAUTH_EXPIRED.read_text())
    assert "OAuth refresh failed" in str(exc.value)
    # The stack trace pi appends is noise to whoever has to fix their login.
    assert "processTicksAndRejections" not in str(exc.value)


def test_pi_expired_oauth_stops_the_run_and_saves_nothing(
    monkeypatch, tmp_path
) -> None:
    """The #132 repro end to end: exit 2 with the /login guidance, no saved run."""
    from typer.testing import CliRunner

    from caliper.main import app

    def fake_run(cmd, **kwargs):
        if cmd[1:] == ["--version"]:
            return _version(cmd)
        return subprocess.CompletedProcess(
            cmd, 0, stdout=_OAUTH_EXPIRED.read_text(), stderr=""
        )

    monkeypatch.setattr("caliper.harness.base.shutil.which", lambda _n: "pi")
    patch_cli_calls(monkeypatch, fake_run)
    # Never let the test copy a real ~/.pi login into the attempt home.
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.chdir(tmp_path)
    spec_file = tmp_path / "basic.eval.yaml"
    spec_file.write_text(
        "tasks:\n"
        "  - name: Probe\n    prompt: Do it\n    activates: []\n"
        "  - name: Judged\n    prompt: Do it\n    expect: it works\n"
    )

    result = CliRunner().invoke(
        app, ["run", str(spec_file), "--k", "1", "--model", "pi:claude-haiku-4-5"]
    )

    assert result.exit_code == 2, result.stdout
    assert "/login" in result.stdout
    assert "Nothing ran" in result.stdout
    assert not (tmp_path / ".caliper").exists()


def _errored_stream(message: str) -> str:
    assistant = {
        "role": "assistant",
        "content": [],
        "usage": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
        "stopReason": "error",
        "errorMessage": message,
    }
    return json.dumps({"type": "message_end", "message": assistant}) + "\n"


def test_pi_stream_error_that_is_not_auth_is_left_to_the_outcome(
    monkeypatch, tmp_path
) -> None:
    # An overloaded provider is a transient infra signal, not a broken login:
    # it must not abort the run.
    result = _run_with_stream(
        monkeypatch, tmp_path, _errored_stream("529 overloaded_error: Overloaded")
    )
    assert result.exit_code == 0
    assert result.refusal is not None
    assert result.refusal.kind is RefusalKind.THROTTLE


@pytest.mark.parametrize(
    "message",
    [
        "You have reached your subscription usage limit. Try again at 4pm.",
        "429 rate_limit_error: authentication rate limit exceeded",
    ],
)
def test_pi_cap_or_throttle_that_mentions_auth_is_not_a_login_failure(
    monkeypatch, tmp_path, message
) -> None:
    # A cap or throttle whose wording brushes an auth marker belongs to the
    # cap/retry handling — not to /login.
    result = _run_with_stream(monkeypatch, tmp_path, _errored_stream(message))
    assert result.exit_code == 0
    assert result.refusal is not None
    assert result.refusal.kind in (RefusalKind.SPENDING_CAP, RefusalKind.THROTTLE)


def test_pi_answer_mentioning_auth_on_a_zero_exit_is_not_a_misconfiguration(
    monkeypatch, tmp_path
) -> None:
    # An agent *writing about* a 401 is answering, not failing to authenticate.
    assistant = {
        "role": "assistant",
        "content": [{"type": "text", "text": "Return 401 Unauthorized here."}],
        "stopReason": "stop",
    }
    stream = json.dumps({"type": "message_end", "message": assistant}) + "\n"
    result = _run_with_stream(monkeypatch, tmp_path, stream)
    assert result.final_output == "Return 401 Unauthorized here."


def test_pi_seeds_models_json_so_custom_providers_resolve(
    monkeypatch, tmp_path
) -> None:
    # A provider defined only in ~/.pi/agent/models.json must exist in the
    # attempt's agent dir too, or `--model pi:<provider>/<model>` fails (#178).
    real = tmp_path / "real" / ".pi" / "agent"
    real.mkdir(parents=True)
    (real / "models.json").write_text('{"providers": {"mock": {}}}')
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "real")

    harness = PiHarness()
    ctx = run_context(isolated_home=str(tmp_path / "home"))
    harness._seed_home(ctx)

    seeded = tmp_path / "home" / ".pi" / "agent" / "models.json"
    assert seeded.read_text() == '{"providers": {"mock": {}}}'


def test_pi_declares_mcp_unsupported_by_design() -> None:
    # pi has no MCP support by design; the harness advertises that permanence via
    # a hint (the run seam turns it into a tailored refusal) and never claims to
    # support MCP.
    harness = PiHarness()
    assert harness.supports_mcp is False
    assert harness.mcp_unsupported_hint is not None
    assert "by design" in harness.mcp_unsupported_hint
