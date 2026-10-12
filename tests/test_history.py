"""Planted history: a task that starts mid-conversation (docs/adr/0036)."""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

import pytest
from conftest import ScriptedHarness, patch_cli_calls, run_context
from pydantic import ValidationError
from typer.testing import CliRunner

from caliper.environment import resolve_environment
from caliper.harness import get_harness
from caliper.harness.base import (
    ConversationTurn,
    HarnessConfigurationError,
    PromptResult,
)
from caliper.harness.claude_code import ClaudeCodeHarness
from caliper.judge.base import JudgeResult
from caliper.judge.eval_judge import EvalJudge, render_judge_prompt
from caliper.main import app
from caliper.runner import run
from caliper.schema.spec import PlantedShellCall, PlantedTurn, load_spec
from caliper.skills import SkillResolutionError, resolve_skills
from caliper.workdir import AttemptWorkdir

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
        ("- hi\n", "turn 1: Input should be"),
        ("- user: hi\n  said: x\n", "turn 1: unknown key `said`"),
        ("- user: hi\n  assistant: yo\n", "turn 1: a turn has exactly one of"),
        ("- user: hi\n  skill: x\n", "turn 1: a turn has exactly one of"),
        ("- user: hi\n  args: x\n", "turn 1: only a skill turn takes `args:`"),
        ("- shell: []\n", "turn 1: a turn has exactly one of"),
        (
            "- user: hi\n  shell:\n    - run: ls\n      output: x\n",
            "turn 1: only an assistant",
        ),
        (
            "- user: hi\n- assistant: ok\n  shell:\n    - run: ls\n",
            "turn 2, shell.0.output: Field required",
        ),
        ("- user: hi\n- assistant: ''\n", "turn 2: an assistant turn needs text"),
        ("- assistant: hi\n", "must start with a user turn"),
    ],
)
def test_a_malformed_history_is_refused_by_turn(tmp_path, text, needle) -> None:
    with pytest.raises(ValidationError, match=needle):
        load_spec(_spec(tmp_path, history_text=text))


def test_a_history_can_open_by_invoking_a_skill(tmp_path) -> None:
    text = "- skill: repro-first\n  args: the double dash\n- assistant: On it.\n"
    task = load_spec(_spec(tmp_path, history_text=text)).tasks[0]

    assert task.planted_history(tmp_path)[0] == PlantedTurn(
        skill="repro-first", args="the double dash"
    )


def test_validate_reports_a_malformed_history(tmp_path) -> None:
    spec = _spec(tmp_path, history_text="- user: hi\n  said: x\n")

    result = CliRunner().invoke(app, ["validate", str(spec)])

    assert result.exit_code != 0
    assert "unknown key `said`" in result.output


# --- the run environment ------------------------------------------------------


def _resolve(spec_path, harness):

    return resolve_environment(
        load_spec(spec_path),
        spec_path,
        harness=harness,
        ablate=[],
        user_customizations=None,
        timeout=30,
    )


def test_each_attempt_is_handed_its_tasks_planted_turns(tmp_path) -> None:

    spec_path = _spec(tmp_path)
    environment = _resolve(spec_path, ScriptedHarness(supports_history=True))
    task = load_spec(spec_path).tasks[0]

    with AttemptWorkdir(tmp_path) as workdir:
        ctx = environment.context(task, 1, workdir)

    assert ctx.history == task.planted_history(tmp_path)
    assert ctx.prompt == "p"


def test_a_skill_turn_must_name_a_declared_skill(tmp_path) -> None:
    spec_path = _spec(tmp_path, history_text="- skill: repro-first\n")

    with pytest.raises(SkillResolutionError, match="repro-first"):
        _resolve(spec_path, ScriptedHarness(supports_history=True))

    result = CliRunner().invoke(app, ["validate", str(spec_path)])
    assert result.exit_code != 0
    assert "repro-first" in result.output


@pytest.mark.parametrize("backend", ["codex", "pi", "hermes"])
def test_a_backend_without_history_refuses_the_spec_by_name(tmp_path, backend) -> None:

    with pytest.raises(HarnessConfigurationError, match=f"'{backend}' backend"):
        _resolve(_spec(tmp_path), get_harness(backend))


# --- claude-code ----------------------------------------------------------------


def _claude_spawn(monkeypatch, tmp_path, history, skill_refs=()):
    """Run claude-code once over ``history``; what it spawned, and the session file."""

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
            skill_refs=list(skill_refs),
        )
    )
    return seen


def test_claude_code_resumes_a_session_written_from_the_history(
    monkeypatch, tmp_path
) -> None:

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


_REPRO_FIRST = (
    "---\nname: repro-first\ndescription: d\ndisable-model-invocation: true\n---\n\n"
    "Write the failing test first.\n"
)


def test_claude_code_plants_a_skill_turn_as_the_expanded_command(
    monkeypatch, tmp_path
) -> None:
    """Expanded from the installed SKILL.md, so the history never copies it."""
    (tmp_path / "skill").mkdir()
    (tmp_path / "skill" / "SKILL.md").write_text(_REPRO_FIRST)
    refs = resolve_skills(["./skill/SKILL.md"], tmp_path)
    history = [
        PlantedTurn(skill="repro-first", args="the double dash"),
        PlantedTurn(assistant="On it."),
    ]

    seen = _claude_spawn(monkeypatch, tmp_path, history, refs)

    planted = seen["lines"][0]["message"]["content"]
    assert planted.startswith("<command-name>/repro-first</command-name>")
    assert "Write the failing test first." in planted
    assert "---" not in planted
    assert planted.endswith("ARGUMENTS: the double dash")
    installed = tmp_path / "home" / ".claude" / "skills" / "repro-first"
    assert f"Base directory for this skill: {installed}" in planted


def test_an_ablated_skill_turn_is_the_bare_command(monkeypatch, tmp_path) -> None:
    """With the skill not installed, what the user typed is all that's left."""
    history = [
        PlantedTurn(skill="repro-first", args="the double dash"),
        PlantedTurn(assistant="On it."),
    ]

    seen = _claude_spawn(monkeypatch, tmp_path, history)

    assert seen["lines"][0]["message"]["content"] == "/repro-first the double dash"


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

    assert "<prior_context>" not in render_judge_prompt("x", [])


def test_the_judge_gets_the_turns_the_attempt_resumed_from(tmp_path) -> None:
    """Read once per run: an edit mid-run can't split the agent from its judge."""

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


def test_the_judge_sees_a_skill_turn_as_the_command_the_user_typed() -> None:
    history = [PlantedTurn(skill="repro-first", args="the double dash")]

    assert "[user] /repro-first the double dash" in render_judge_prompt(
        "x", [], history
    )
