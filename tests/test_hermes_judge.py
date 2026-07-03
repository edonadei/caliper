from __future__ import annotations

import subprocess

from caliper.harness.base import ConversationTurn
from caliper.judge.hermes_judge import evaluate_with_hermes
from caliper.judge.script_assert import EvalJudge
from caliper.schema.spec import TaskSpec


def test_hermes_backend_is_a_valid_judge(monkeypatch, tmp_path) -> None:
    """Regression: `--judge-model hermes` must dispatch, not 'Unknown judge backend'."""
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(
            cmd,
            0,
            stdout='{"mode": "verdict", "passed": true, "reasoning": "ok"}',
            stderr="",
        )

    monkeypatch.setattr(
        "caliper.judge.hermes_judge.shutil.which", lambda _: "/usr/bin/hermes"
    )
    monkeypatch.setattr("caliper.judge.hermes_judge.subprocess.run", fake_run)

    result = EvalJudge(backend="hermes", model="anthropic/claude-sonnet-4.6").evaluate(
        task=TaskSpec(id="t1", name="t", prompt="p", expect="says ok"),
        transcript=[ConversationTurn(role="assistant", content="ok")],
        final_output="ok",
        spec_dir=str(tmp_path),
    )

    assert result.passed is True
    assert result.autorater_passed is True
    cmd = captured["cmd"]
    assert cmd[0] == "/usr/bin/hermes"
    assert "-z" in cmd
    assert "--ignore-rules" in cmd
    assert cmd[cmd.index("--model") + 1] == "anthropic/claude-sonnet-4.6"


def test_hermes_judge_strips_markdown_fence(monkeypatch, tmp_path) -> None:
    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(
            cmd,
            0,
            stdout='```json\n{"mode": "verdict", "passed": false, "reasoning": "no"}\n```',
            stderr="",
        )

    monkeypatch.setattr(
        "caliper.judge.hermes_judge.shutil.which", lambda _: "/usr/bin/hermes"
    )
    monkeypatch.setattr("caliper.judge.hermes_judge.subprocess.run", fake_run)

    passed, reasoning, errored, _model = evaluate_with_hermes(
        expect="anything", transcript=[], model=None, spec_dir=str(tmp_path)
    )

    assert (passed, errored) == (False, False)
    assert reasoning == "no"


def test_hermes_judge_reports_cli_error_as_errored(monkeypatch, tmp_path) -> None:
    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="not logged in")

    monkeypatch.setattr(
        "caliper.judge.hermes_judge.shutil.which", lambda _: "/usr/bin/hermes"
    )
    monkeypatch.setattr("caliper.judge.hermes_judge.subprocess.run", fake_run)

    passed, reasoning, errored, _model = evaluate_with_hermes(
        expect="anything", transcript=[], model=None, spec_dir=str(tmp_path)
    )

    assert (passed, errored) == (False, True)
    assert "not logged in" in reasoning


def test_hermes_judge_missing_cli_errors(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("caliper.judge.hermes_judge.shutil.which", lambda _: None)
    monkeypatch.delenv("HERMES_CLI_PATH", raising=False)

    passed, reasoning, errored, _model = evaluate_with_hermes(
        expect="anything", transcript=[], model=None, spec_dir=str(tmp_path)
    )

    assert (passed, errored) == (False, True)
    assert "hermes CLI not found" in reasoning
