from __future__ import annotations

import json
import subprocess

import pytest

from caliper.harness.base import HarnessConfigurationError
from caliper.harness.hermes import HermesHarness


def _version(cmd):
    return subprocess.CompletedProcess(
        cmd, 0, stdout="Hermes Agent v0.18.0\n", stderr=""
    )


def _fake_home(tmp_path):
    """A fake ~/.hermes with auth/config *and* persona/memory to strip."""
    real = tmp_path / "realhome" / ".hermes"
    real.mkdir(parents=True)
    (real / "auth.json").write_text("{}")
    (real / "config.yaml").write_text("model:\n  provider: anthropic\n")
    (real / ".env").write_text("SECRET=1\n")
    (real / "SOUL.md").write_text("You are a quirky persona.")
    (real / "MEMORY.md").write_text("The user's cat is named Mochi.")
    return tmp_path / "realhome"


def _install(monkeypatch, home, on_run):
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("HERMES_CLI_PATH", raising=False)
    monkeypatch.setattr("caliper.harness.hermes.shutil.which", lambda _n: "hermes")
    monkeypatch.setattr("caliper.harness.base.subprocess.run", on_run)


def test_hermes_seeds_only_neutral_config_and_ignores_rules(
    monkeypatch, tmp_path
) -> None:
    home = _fake_home(tmp_path)
    iso = tmp_path / "iso"
    iso.mkdir()
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        if cmd[1:] == ["--version"]:
            return _version(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    _install(monkeypatch, home, fake_run)

    HermesHarness().run(
        task_id="task-001",
        attempt=1,
        prompt="Hello",
        skill_path=None,
        model=None,
        timeout=30,
        isolated_home=str(iso),
    )

    seeded = iso / ".hermes"
    # auth/config/.env are copied verbatim so the agent can authenticate...
    assert (seeded / "auth.json").exists()
    assert (seeded / "config.yaml").exists()
    assert (seeded / ".env").exists()
    # ...but persona and memory are never seeded — Hermes is stripped to neutral.
    assert not (seeded / "SOUL.md").exists()
    assert not (seeded / "MEMORY.md").exists()

    run_cmd, run_kwargs = calls[1]
    assert run_cmd[0] == "/bin/sh"
    script = run_cmd[2]
    assert "--ignore-rules" in script
    env = run_kwargs["env"]
    assert env["HERMES_HOME"] == str(seeded)
    assert env["HOME"] == str(iso)
    assert env["CALIPER_PROMPT"] == "Hello"


def test_hermes_stages_skill_and_passes_skills_flag(monkeypatch, tmp_path) -> None:
    home = _fake_home(tmp_path)
    iso = tmp_path / "iso"
    iso.mkdir()
    # The runner stages the skill dir into isolated_home; simulate that.
    (iso / "SKILL.md").write_text(
        "---\nname: my-skill\ndescription: test\n---\n\nDo the thing.\n"
    )
    (iso / "REFERENCE.md").write_text("Extra detail.")
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        if cmd[1:] == ["--version"]:
            return _version(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    _install(monkeypatch, home, fake_run)

    HermesHarness().run(
        task_id="task-001",
        attempt=1,
        prompt="Do it",
        skill_path=str(iso / "SKILL.md"),
        model=None,
        timeout=30,
        isolated_home=str(iso),
    )

    staged = iso / ".hermes" / "skills" / "my-skill"
    assert (staged / "SKILL.md").exists()
    # Progressive-disclosure siblings travel with the skill.
    assert (staged / "REFERENCE.md").exists()

    run_cmd, run_kwargs = calls[1]
    script = run_cmd[2]
    assert "--skills" in script
    assert run_kwargs["env"]["CALIPER_SKILL"] == "my-skill"


def test_hermes_no_skills_flag_without_skill(monkeypatch, tmp_path) -> None:
    home = _fake_home(tmp_path)
    iso = tmp_path / "iso"
    iso.mkdir()

    def fake_run(cmd, **kwargs):
        if cmd[1:] == ["--version"]:
            return _version(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    _install(monkeypatch, home, fake_run)

    result_calls = []
    monkeypatch.setattr(
        "caliper.harness.base.subprocess.run",
        lambda cmd, **kw: (result_calls.append((cmd, kw)) or fake_run(cmd, **kw)),
    )

    HermesHarness().run(
        task_id="task-001",
        attempt=1,
        prompt="Hello",
        skill_path=None,
        model=None,
        timeout=30,
        isolated_home=str(iso),
    )

    script = result_calls[1][0][2]
    assert "--skills" not in script
    assert "CALIPER_SKILL" not in result_calls[1][1]["env"]


def test_hermes_parses_export_trajectory(monkeypatch, tmp_path) -> None:
    home = _fake_home(tmp_path)
    iso = tmp_path / "iso"
    iso.mkdir()

    export = {
        "id": "20260703_000000_abc",
        "source": "cli",
        "model": "stepfun/step-3.7-flash:free",
        "messages": [
            {"role": "user", "content": "Run echo.", "tool_calls": None},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "terminal",
                            "arguments": '{"command": "echo HELLO123"}',
                        },
                    }
                ],
            },
            {
                "role": "tool",
                "tool_name": "terminal",
                "tool_call_id": "call_1",
                "content": '{"output": "HELLO123", "exit_code": 0}',
            },
            {"role": "assistant", "content": "Done: HELLO123", "tool_calls": None},
        ],
    }

    def fake_run(cmd, **kwargs):
        if cmd[1:] == ["--version"]:
            return _version(cmd)
        return subprocess.CompletedProcess(
            cmd, 0, stdout=json.dumps(export), stderr="Done: HELLO123"
        )

    _install(monkeypatch, home, fake_run)

    result = HermesHarness().run(
        task_id="task-001",
        attempt=1,
        prompt="Run echo",
        skill_path=None,
        model=None,
        timeout=30,
        isolated_home=str(iso),
    )

    assert result.final_output == "Done: HELLO123"
    assert [t.role for t in result.transcript] == [
        "user",
        "tool_use",
        "tool_result",
        "assistant",
    ]
    tool_use = result.transcript[1]
    assert tool_use.tool_name == "terminal"
    # tool_input is parsed from the JSON-string arguments so the cheat-detector
    # can inspect any paths/commands.
    assert tool_use.tool_input == {"command": "echo HELLO123"}
    assert "HELLO123" in result.transcript[2].tool_output
    # A successful run reports no error even though the final text is on stderr.
    assert result.error is None
    assert result.exit_code == 0
    # The concrete model is recovered from the export's top-level `model` field,
    # so a default-model run still records what actually ran.
    assert result.resolved_model == "stepfun/step-3.7-flash:free"


def test_hermes_run_captures_token_usage_end_to_end(monkeypatch, tmp_path) -> None:
    """Smoke test: a full hermes run reads the export record's session-total token
    fields into AttemptResult.usage.

    Hermes aggregates usage per session, exposing flat top-level ``input_tokens``
    / ``output_tokens`` / ``cache_read_tokens`` / ``cache_write_tokens`` on the
    export record — the shape captured from a real ``hermes sessions export``.
    """
    home = _fake_home(tmp_path)
    iso = tmp_path / "iso"
    iso.mkdir()

    export = {
        "id": "20260704_000000_xyz",
        "source": "cli",
        "model": "claude-sonnet-4-6",
        "input_tokens": 800,
        "output_tokens": 60,
        "cache_read_tokens": 25,
        "cache_write_tokens": 10,
        "reasoning_tokens": 5,
        "actual_cost_usd": None,
        "messages": [
            {"role": "user", "content": "Write hello.", "tool_calls": None},
            {"role": "assistant", "content": "Wrote hello.", "tool_calls": None},
        ],
    }

    def fake_run(cmd, **kwargs):
        if cmd[1:] == ["--version"]:
            return _version(cmd)
        return subprocess.CompletedProcess(
            cmd, 0, stdout=json.dumps(export), stderr="Wrote hello."
        )

    _install(monkeypatch, home, fake_run)

    result = HermesHarness().run(
        task_id="task-001",
        attempt=1,
        prompt="Write hello to a file",
        skill_path=None,
        model=None,
        timeout=30,
        isolated_home=str(iso),
    )

    assert result.final_output == "Wrote hello."
    assert result.usage is not None
    assert result.usage.input_tokens == 800  # non-cached
    assert result.usage.output_tokens == 60
    assert result.usage.cache_read_tokens == 25
    assert result.usage.cache_creation_tokens == 10  # from cache_write_tokens
    # reasoning_tokens has no TokenUsage field; total is the four disjoint fields.
    assert result.usage.total_tokens == 895


def test_hermes_missing_cli_raises_configuration_error(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("caliper.harness.hermes.shutil.which", lambda _n: None)
    monkeypatch.delenv("HERMES_CLI_PATH", raising=False)

    with pytest.raises(HarnessConfigurationError, match="hermes CLI is not available"):
        HermesHarness().run(
            task_id="task-001",
            attempt=1,
            prompt="Hello",
            skill_path=None,
            model=None,
            timeout=12,
            isolated_home=str(tmp_path),
        )


def test_hermes_credit_failure_raises_configuration_error(
    monkeypatch, tmp_path
) -> None:
    home = _fake_home(tmp_path)
    iso = tmp_path / "iso"
    iso.mkdir()

    def fake_run(cmd, **kwargs):
        if cmd[1:] == ["--version"]:
            return _version(cmd)
        return subprocess.CompletedProcess(
            cmd, 1, stdout="", stderr="HTTP 400: You're out of extra usage."
        )

    _install(monkeypatch, home, fake_run)

    with pytest.raises(HarnessConfigurationError, match="provider/credential"):
        HermesHarness().run(
            task_id="task-001",
            attempt=1,
            prompt="Hello",
            skill_path=None,
            model=None,
            timeout=12,
            isolated_home=str(iso),
        )
