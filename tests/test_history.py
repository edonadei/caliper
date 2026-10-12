"""Planted history: a task that starts mid-conversation (docs/adr/0036)."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from caliper.main import app
from caliper.schema.spec import PlantedShellCall, PlantedTurn, load_spec

_HISTORY = """\
- user: Migrate billing to the ledger API. Start by finding the call sites.
- assistant: Searching for the old client.
  shell:
    - run: grep -rn "BillingClient(" src
      output: |
        src/invoice.py:12: client = BillingClient()
- assistant: One call site, in invoice.py.
"""


def _spec(tmp_path, history_text: str | None = _HISTORY, history: str = "./h.yaml"):
    if history_text is not None:
        (tmp_path / "h.yaml").write_text(history_text)
    spec = tmp_path / "s.eval.yaml"
    spec.write_text(
        f"tasks:\n  - name: t\n    history: {history}\n    prompt: p\n    expect: x\n"
    )
    return spec


# --- the spec -----------------------------------------------------------------


def test_a_task_names_its_history_by_path_and_the_turns_load(tmp_path) -> None:
    task = load_spec(_spec(tmp_path)).tasks[0]

    assert task.history == "./h.yaml"
    assert task.planted_history(tmp_path) == [
        PlantedTurn(
            user="Migrate billing to the ledger API. Start by finding the call sites."
        ),
        PlantedTurn(
            assistant="Searching for the old client.",
            shell=[
                PlantedShellCall(
                    run='grep -rn "BillingClient(" src',
                    output="src/invoice.py:12: client = BillingClient()\n",
                )
            ],
        ),
        PlantedTurn(assistant="One call site, in invoice.py."),
    ]


def test_a_task_without_history_has_none(tmp_path) -> None:
    spec = tmp_path / "s.eval.yaml"
    spec.write_text("tasks:\n  - name: t\n    prompt: p\n    expect: x\n")
    assert load_spec(spec).tasks[0].planted_history(tmp_path) is None


def test_a_missing_history_file_is_refused(tmp_path) -> None:
    with pytest.raises(ValidationError, match="history file not found"):
        load_spec(_spec(tmp_path, history_text=None))


@pytest.mark.parametrize(
    ("text", "needle"),
    [
        ("", "at least one turn"),
        ("user: hi\n", "a list of turns"),
        ("- hi\n", "turn 1 must be a mapping"),
        ("- user: hi\n  said: x\n", "turn 1: unknown key `said`"),
        ("- user: hi\n  assistant: yo\n", "turn 1 must have exactly one of"),
        ("- shell: []\n", "turn 1 must have exactly one of"),
        ("- user: hi\n  shell:\n    - run: ls\n      output: x\n", "turn 1"),
        ("- user: hi\n- assistant: ok\n  shell:\n    - run: ls\n", "turn 2"),
        ("- user: hi\n- assistant: ''\n", "turn 2"),
        ("- assistant: hi\n", "must start with a user turn"),
    ],
)
def test_a_malformed_history_is_refused_by_turn(tmp_path, text, needle) -> None:
    with pytest.raises(ValidationError, match=needle):
        load_spec(_spec(tmp_path, history_text=text))


def test_validate_reports_a_malformed_history(tmp_path) -> None:
    spec = _spec(tmp_path, history_text="- user: hi\n  said: x\n")

    result = CliRunner().invoke(app, ["validate", str(spec)])

    assert result.exit_code != 0
    assert "unknown key `said`" in result.output


# --- the run environment ------------------------------------------------------


def _resolve(spec_path, harness):
    from caliper.environment import resolve_environment

    return resolve_environment(
        load_spec(spec_path),
        spec_path,
        harness=harness,
        ablate=[],
        user_customizations=None,
        timeout=30,
    )


def test_each_attempt_is_handed_its_tasks_planted_turns(tmp_path) -> None:
    from conftest import ScriptedHarness

    from caliper.workdir import AttemptWorkdir

    spec_path = _spec(tmp_path)
    environment = _resolve(spec_path, ScriptedHarness(supports_history=True))
    task = load_spec(spec_path).tasks[0]

    with AttemptWorkdir(tmp_path) as workdir:
        ctx = environment.context(task, 1, workdir)

    assert ctx.history == task.planted_history(tmp_path)
    assert ctx.prompt == "p"


@pytest.mark.parametrize("backend", ["codex", "pi", "hermes"])
def test_a_backend_without_history_refuses_the_spec_by_name(tmp_path, backend) -> None:
    from caliper.harness import get_harness
    from caliper.harness.base import HarnessConfigurationError

    with pytest.raises(HarnessConfigurationError, match=f"'{backend}' backend"):
        _resolve(_spec(tmp_path), get_harness(backend))


# --- claude-code ----------------------------------------------------------------


def _claude_spawn(monkeypatch, tmp_path, history):
    """Run claude-code once over ``history``; what it spawned, and the session file."""
    import json
    import subprocess

    from conftest import patch_cli_calls, run_context

    from caliper.harness.claude_code import ClaudeCodeHarness

    seen: dict = {}

    def fake_run(cmd, **kwargs):
        if cmd[0] != "claude":
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="")
        seen["cmd"] = cmd
        if "--resume" in cmd:
            session_id = cmd[cmd.index("--resume") + 1]
            [path] = (tmp_path / "home" / ".claude" / "projects").glob(
                f"*/{session_id}.jsonl"
            )
            seen["path"] = path
            seen["lines"] = [json.loads(line) for line in path.read_text().splitlines()]
        stdout = json.dumps({"type": "result", "result": "done"})
        return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

    patch_cli_calls(monkeypatch, fake_run)
    (tmp_path / "home").mkdir(parents=True)
    (tmp_path / "work").mkdir()
    ClaudeCodeHarness().run(
        run_context(
            prompt="Finish it.",
            isolated_home=str(tmp_path / "home"),
            workdir=str(tmp_path / "work"),
            history=history,
        )
    )
    return seen


def test_claude_code_resumes_a_session_written_from_the_history(
    monkeypatch, tmp_path
) -> None:
    import os
    import re

    history = load_spec(_spec(tmp_path)).tasks[0].planted_history(tmp_path)
    seen = _claude_spawn(monkeypatch, tmp_path, history)

    cmd = seen["cmd"]
    assert cmd[cmd.index("-p") + 1] == "Finish it."
    # Stored where the CLI looks for the workdir's sessions: its real path,
    # every other character a dash.
    workdir = os.path.realpath(tmp_path / "work")
    assert seen["path"].parent.name == re.sub(r"[^A-Za-z0-9]", "-", workdir)

    lines = seen["lines"]
    assert [(line["type"], line["message"]["role"]) for line in lines] == [
        ("user", "user"),
        ("assistant", "assistant"),
        ("user", "user"),
        ("assistant", "assistant"),
    ]
    # One chain, in one session, at the attempt's own workdir.
    assert [line["parentUuid"] for line in lines] == [
        None,
        *(line["uuid"] for line in lines[:-1]),
    ]
    assert {line["sessionId"] for line in lines} == {seen["path"].stem}
    assert {line["cwd"] for line in lines} == {workdir}
    # The CLI ignores a session whose lines carry no timestamp.
    assert all(line["timestamp"] for line in lines)

    first, call, result, last = (line["message"]["content"] for line in lines)
    assert first == history[0].user
    assert call[0] == {"type": "text", "text": "Searching for the old client."}
    assert call[1]["type"] == "tool_use"
    assert call[1]["name"] == "Bash"
    assert call[1]["input"] == {"command": 'grep -rn "BillingClient(" src'}
    assert result == [
        {
            "type": "tool_result",
            "tool_use_id": call[1]["id"],
            "content": "src/invoice.py:12: client = BillingClient()\n",
        }
    ]
    assert last == [{"type": "text", "text": "One call site, in invoice.py."}]


def test_claude_code_starts_fresh_without_a_history(monkeypatch, tmp_path) -> None:
    seen = _claude_spawn(monkeypatch, tmp_path, None)

    assert "--resume" not in seen["cmd"]
    assert not (tmp_path / "home" / ".claude" / "projects").exists()


def test_parallel_attempts_write_separate_sessions(monkeypatch, tmp_path) -> None:
    history = [PlantedTurn(user="hi"), PlantedTurn(assistant="hello")]
    first = _claude_spawn(monkeypatch, tmp_path / "a", history)
    second = _claude_spawn(monkeypatch, tmp_path / "b", history)

    assert first["path"] != second["path"]
    assert first["lines"][0]["sessionId"] != second["lines"][0]["sessionId"]


# --- the judge ------------------------------------------------------------------


def test_the_judge_sees_the_planted_turns_as_prior_context(attempt_workdir) -> None:
    from caliper.harness.base import ConversationTurn, PromptResult
    from caliper.judge.eval_judge import EvalJudge

    prompts: list[str] = []

    class Autorater:
        def run_prompt(self, prompt, *, model=None, cwd, timeout=60):
            prompts.append(prompt)
            return PromptResult(text='{"mode": "verdict", "passed": true}')

    task = load_spec(_spec(Path(attempt_workdir.spec_dir))).tasks[0]
    EvalJudge(backend="codex", harness=Autorater()).evaluate(
        task=task,
        transcript=[ConversationTurn(role="assistant", content="Migrated refund.py")],
        workdir=attempt_workdir,
        history=task.planted_history(Path(attempt_workdir.spec_dir)),
    )

    [prompt] = prompts
    prior, _, graded = prompt.partition("<transcript>")
    # Labelled as the author's, ahead of and apart from the agent's transcript.
    assert "<prior_context>" in prior
    assert "not the agent's" in prior
    assert "[user] Migrate billing to the ledger API." in prior
    assert '[tool_use: shell] grep -rn "BillingClient(" src' in prior
    assert "[tool_result] src/invoice.py:12" in prior
    assert "[assistant] One call site, in invoice.py." in prior
    assert "Migrated refund.py" in graded
    assert "Migrate billing" not in graded


def test_the_judge_prompt_has_no_prior_context_without_a_history() -> None:
    from caliper.judge.eval_judge import render_judge_prompt

    assert "<prior_context>" not in render_judge_prompt("x", [])


def test_the_judge_gets_the_turns_the_attempt_resumed_from(tmp_path) -> None:
    """Read once per run: an edit mid-run can't split the agent from its judge."""
    from conftest import ScriptedHarness

    from caliper.judge.base import JudgeResult
    from caliper.runner import run

    spec_path = _spec(tmp_path)
    harness = ScriptedHarness(supports_history=True)
    seen: list = []

    class Judge:
        backend, model, prompt_version = "test", None, None

        def evaluate(self, task, transcript, workdir, history=None):
            seen.append(history)
            (tmp_path / "h.yaml").write_text("- user: edited\n")
            return JudgeResult(passed=True)

    run(load_spec(spec_path), spec_path, harness, Judge(), k=2, workers=1)

    planted = [ctx.history for ctx in harness.contexts]
    assert seen == planted
    assert planted[0] == planted[1]
    assert planted[0][0].user.startswith("Migrate billing")
