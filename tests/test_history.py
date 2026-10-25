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
from caliper.harness.claude_code import CLAUDE_SESSION, ClaudeCodeHarness
from caliper.harness.codex import CODEX_ROLLOUT, CodexHarness
from caliper.harness.pi import PI_SESSION, PiHarness
from caliper.judge.base import JudgeResult
from caliper.judge.eval_judge import EvalJudge, render_judge_prompt
from caliper.main import app
from caliper.runner import run
from caliper.schema.spec import PlantedShellCall, PlantedTurn, load_spec
from caliper.skills import resolve_skills
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


def _spec(
    tmp_path, history_text: str | None = _HISTORY, history: str = "./h.history.yaml"
):
    if history_text is not None:
        (tmp_path / "h.history.yaml").write_text(history_text)
    spec = tmp_path / "s.eval.yaml"
    spec.write_text(
        f"tasks:\n  - name: t\n    history: {history}\n    prompt: p\n    expect: x\n"
    )
    return spec


# --- the spec -----------------------------------------------------------------


def test_a_task_names_its_history_by_path_and_the_turns_load(tmp_path) -> None:
    task = load_spec(_spec(tmp_path)).tasks[0]

    assert task.history == "./h.history.yaml"
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


@pytest.mark.parametrize(
    ("text", "command"),
    [
        ("/repro-first the double dash", ("repro-first", "the double dash")),
        ("/repro-first", ("repro-first", "")),
        ("/repro-first\nline one\nline two", ("repro-first", "line one\nline two")),
        ("/tmp/out.log is full", None),
        ("Run /repro-first please", None),
    ],
)
def test_a_user_turn_opening_with_a_name_is_a_slash_command(text, command) -> None:
    assert PlantedTurn(user=text).slash_command == command


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


def test_a_slash_command_naming_no_declared_skill_is_warned_about(tmp_path) -> None:
    """Plain text, not refused: a user turn may open with a path."""
    spec_path = _spec(tmp_path, history_text="- user: /repro-frist the bug\n")
    warnings: list[str] = []

    resolve_environment(
        load_spec(spec_path),
        spec_path,
        harness=ScriptedHarness(supports_history=True),
        ablate=[],
        user_customizations=None,
        timeout=30,
        on_warning=warnings.append,
    )

    [warning] = warnings
    assert "/repro-frist" in warning
    result = CliRunner().invoke(app, ["validate", str(spec_path)])
    assert result.exit_code == 0
    assert "repro-frist" in result.output


def test_a_backend_without_history_refuses_the_spec_by_name(tmp_path) -> None:

    with pytest.raises(HarnessConfigurationError, match="'hermes' backend") as e:
        _resolve(_spec(tmp_path), get_harness("hermes"))
    # Pointed at the backends that can resume it.
    for backend in ("claude-code", "codex", "pi"):
        assert f"--model {backend}" in str(e.value)


# --- the planted session, on every backend that resumes one ----------------------

# Each backend's session format, beside the harness whose skills root it reads.
_FORMATS = [
    pytest.param(ClaudeCodeHarness(), CLAUDE_SESSION, id="claude-code"),
    pytest.param(CodexHarness(), CODEX_ROLLOUT, id="codex"),
    pytest.param(PiHarness(), PI_SESSION, id="pi"),
]


def _plant(tmp_path, harness, fmt, history, skill_refs=()):
    """Plant ``history`` for one attempt; the handle and the file's lines."""
    ctx = run_context(
        isolated_home=str(tmp_path / "home"),
        workdir=str(tmp_path / "work"),
        history=history,
        skill_refs=list(skill_refs),
    )
    (tmp_path / "work").mkdir(parents=True, exist_ok=True)
    planted = fmt.plant(ctx, harness.skills_root(ctx))
    lines = [json.loads(line) for line in planted.path.read_text().splitlines()]
    return planted, lines


@pytest.mark.parametrize(("harness", "fmt"), _FORMATS)
def test_a_planted_session_is_written_where_its_handle_points(
    harness, fmt, tmp_path
) -> None:
    history = [PlantedTurn(user="hi"), PlantedTurn(assistant="hello")]

    planted, lines = _plant(tmp_path, harness, fmt, history)

    assert planted.path.is_relative_to(tmp_path / "home")
    assert planted.id in planted.path.name
    assert {"hi", "hello"} <= set(_strings(lines))


def _strings(value) -> list[str]:
    """Every string in a session's lines, whatever the format nests them in."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        value = list(value.values())
    if isinstance(value, list):
        return [s for item in value for s in _strings(item)]
    return []


_REPRO_FIRST = (
    "---\nname: repro-first\ndescription: d\ndisable-model-invocation: true\n---\n\n"
    "Write the failing test first.\n"
)


def _install(harness, tmp_path, *names):
    """Declare ``repro-first``; install a copy that differs from its source."""
    (tmp_path / "skill").mkdir()
    (tmp_path / "skill" / "SKILL.md").write_text(_REPRO_FIRST)
    refs = resolve_skills(["./skill/SKILL.md"], tmp_path)
    root = harness.skills_root(run_context(isolated_home=str(tmp_path / "home")))
    for name in names:
        (root / name).mkdir(parents=True, exist_ok=True)
        (root / name / "SKILL.md").write_text(
            _REPRO_FIRST.replace("Write the failing test first.", "Installed copy.")
        )
    return refs


@pytest.mark.parametrize(("harness", "fmt"), _FORMATS)
def test_a_declared_slash_command_plants_the_installed_skill(
    harness, fmt, tmp_path
) -> None:
    """Read at run time, so the history never copies the skill (docs/adr/0036)."""
    refs = _install(harness, tmp_path, "repro-first")
    history = [PlantedTurn(user="/repro-first go"), PlantedTurn(assistant="On it.")]

    _, lines = _plant(tmp_path, harness, fmt, history, refs)

    planted = "\n".join(_strings(lines))
    assert "Installed copy." in planted
    assert "Write the failing test first." not in planted
    assert "/repro-first go" not in _strings(lines)


@pytest.mark.parametrize(("harness", "fmt"), _FORMATS)
def test_an_ablated_skills_command_stays_as_typed(harness, fmt, tmp_path) -> None:
    """With the skill not installed, the CLI wouldn't expand it either."""
    history = [PlantedTurn(user="/repro-first go"), PlantedTurn(assistant="On it.")]

    _, lines = _plant(tmp_path, harness, fmt, history)

    assert "/repro-first go" in _strings(lines)


@pytest.mark.parametrize(("harness", "fmt"), _FORMATS)
def test_a_slash_command_matches_a_declared_skill_case_sensitively(
    harness, fmt, tmp_path
) -> None:
    """``/REPRO-FIRST`` stays as typed on every OS, as validate warns it will.

    The skills root holds a ``REPRO-FIRST/SKILL.md``, which is what a
    case-insensitive file system resolves for the installed ``repro-first``, so
    this holds without depending on the machine's file system.
    """
    refs = _install(harness, tmp_path, "repro-first", "REPRO-FIRST")
    history = [PlantedTurn(user="/REPRO-FIRST go"), PlantedTurn(assistant="On it.")]

    _, lines = _plant(tmp_path, harness, fmt, history, refs)

    assert "/REPRO-FIRST go" in _strings(lines)
    assert "Installed copy." not in "\n".join(_strings(lines))


@pytest.mark.parametrize(("harness", "fmt"), _FORMATS)
def test_each_planted_call_is_answered_by_its_own_result(
    harness, fmt, tmp_path
) -> None:
    """One id per call, on its call and its result, across turns and within one."""
    history = [
        PlantedTurn(user="Look around."),
        PlantedTurn(
            assistant="Two at once.",
            shell=[
                PlantedShellCall(run="ls", output="a.py"),
                PlantedShellCall(run="pwd", output="/work"),
            ],
        ),
        PlantedTurn(
            assistant="", shell=[PlantedShellCall(run="cat a.py", output="x = 1")]
        ),
    ]

    _, lines = _plant(tmp_path, harness, fmt, history)

    ids = [s for s in _strings(lines) if s.startswith(fmt.call_prefix)]
    assert len(set(ids)) == 3
    assert all(ids.count(call_id) == 2 for call_id in ids)


@pytest.mark.parametrize(("harness", "fmt"), _FORMATS)
def test_each_planting_is_a_session_of_its_own(harness, fmt, tmp_path) -> None:
    """Parallel attempts, and a retried one, never share a file."""
    history = [PlantedTurn(user="hi"), PlantedTurn(assistant="hello")]

    first, _ = _plant(tmp_path, harness, fmt, history)
    second, _ = _plant(tmp_path, harness, fmt, history)

    assert first.id != second.id
    assert first.path != second.path
    assert first.path.exists()


# --- each backend's format --------------------------------------------------------


def _installed_md() -> str:
    return _REPRO_FIRST.replace("Write the failing test first.", "Installed copy.")


def test_claude_code_writes_a_chained_session_under_the_workdirs_slug(
    tmp_path,
) -> None:
    history = load_spec(_spec(tmp_path)).tasks[0].planted_history(tmp_path)

    planted, lines = _plant(tmp_path, ClaudeCodeHarness(), CLAUDE_SESSION, history)

    # Stored where the CLI looks for the workdir's sessions: its real path,
    # every other character a dash.
    workdir = os.path.realpath(tmp_path / "work")
    projects = tmp_path / "home" / ".claude" / "projects"
    slug = re.sub(r"[^A-Za-z0-9]", "-", workdir)
    assert planted.path == projects / slug / f"{planted.id}.jsonl"

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
    assert {line["sessionId"] for line in lines} == {planted.id}
    assert {line["cwd"] for line in lines} == {workdir}
    # The CLI ignores a session whose lines carry no timestamp.
    assert all(line["timestamp"] for line in lines)

    first, call, result, last = (line["message"]["content"] for line in lines)
    assert first == history[0].user
    assert call[0] == {"type": "text", "text": "Searching for the old client."}
    assert call[1]["type"] == "tool_use"
    assert call[1]["id"].startswith("toolu_")
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


def test_claude_code_plants_a_slash_command_as_the_expanded_skill(tmp_path) -> None:
    harness = ClaudeCodeHarness()
    refs = _install(harness, tmp_path, "repro-first")
    history = [
        PlantedTurn(user="/repro-first the double dash"),
        PlantedTurn(assistant="On it."),
    ]

    _, lines = _plant(tmp_path, harness, CLAUDE_SESSION, history, refs)

    installed = tmp_path / "home" / ".claude" / "skills" / "repro-first"
    assert lines[0]["message"]["content"] == (
        "<command-name>/repro-first</command-name>\n"
        "<command-args>the double dash</command-args>\n\n"
        f"Base directory for this skill: {installed}\n\n"
        "Installed copy.\n\n"
        "ARGUMENTS: the double dash"
    )


def test_codex_writes_a_rollout_after_its_session_meta(tmp_path) -> None:
    history = load_spec(_spec(tmp_path)).tasks[0].planted_history(tmp_path)

    planted, lines = _plant(tmp_path, CodexHarness(), CODEX_ROLLOUT, history)

    # Found by scanning sessions/, so a dated path and no index.
    sessions = tmp_path / "home" / ".codex" / "sessions"
    year, month, day, name = planted.path.relative_to(sessions).parts
    assert name.startswith(f"rollout-{year}-{month}-{day}T")
    assert name.endswith(f"-{planted.id}.jsonl")

    meta, *items = lines
    assert meta["type"] == "session_meta"
    assert meta["payload"]["id"] == meta["payload"]["session_id"] == planted.id
    assert meta["payload"]["cwd"] == os.path.realpath(tmp_path / "work")
    assert meta["payload"]["source"] == "exec"
    assert all(line["timestamp"] for line in lines)
    assert {line["type"] for line in items} == {"response_item"}

    first, said, call, output, last = (line["payload"] for line in items)
    assert first == {
        "type": "message",
        "role": "user",
        "content": [{"type": "input_text", "text": history[0].user}],
    }
    assert said == {
        "type": "message",
        "role": "assistant",
        "content": [{"type": "output_text", "text": "Searching for the old client."}],
    }
    assert call["type"] == "function_call"
    assert call["name"] == "exec_command"
    assert json.loads(call["arguments"]) == {"cmd": 'grep -rn "BillingClient(" src'}
    assert output == {
        "type": "function_call_output",
        "call_id": call["call_id"],
        "output": "src/invoice.py:12: client = BillingClient()\n",
    }
    assert last["content"] == [
        {"type": "output_text", "text": "One call site, in invoice.py."}
    ]


def test_codex_plants_a_slash_command_as_the_invoked_skill(tmp_path) -> None:
    """Codex's own shape: the typed $name, then the whole installed SKILL.md."""
    harness = CodexHarness()
    refs = _install(harness, tmp_path, "repro-first")
    history = [
        PlantedTurn(user="/repro-first the double dash"),
        PlantedTurn(assistant="On it."),
    ]

    _, lines = _plant(tmp_path, harness, CODEX_ROLLOUT, history, refs)

    typed, skill = (line["payload"]["content"][0]["text"] for line in lines[1:3])
    assert typed == "$repro-first the double dash"
    installed = tmp_path / "home" / ".codex" / "skills" / "repro-first" / "SKILL.md"
    assert skill == (
        f"<skill>\n<name>repro-first</name>\n<path>{installed}</path>\n"
        f"{_installed_md()}\n</skill>"
    )


def test_pi_writes_a_chained_session_under_its_agent_dirs_sessions(tmp_path) -> None:
    history = load_spec(_spec(tmp_path)).tasks[0].planted_history(tmp_path)

    planted, lines = _plant(tmp_path, PiHarness(), PI_SESSION, history)

    # In the attempt's own agent dir, never the real ~/.pi, and under sessions/:
    # pi moves a session file left in the agent dir's root.
    agent_dir = tmp_path / "home" / ".pi" / "agent"
    assert planted.path.parent == agent_dir / "sessions"

    header, *entries = lines
    assert header["type"] == "session"
    assert header["version"] == 3
    assert header["id"] == planted.id
    assert header["timestamp"]
    assert header["cwd"] == os.path.realpath(tmp_path / "work")
    assert {entry["type"] for entry in entries} == {"message"}
    assert [entry["parentId"] for entry in entries] == [
        None,
        *(entry["id"] for entry in entries[:-1]),
    ]
    assert all(entry["timestamp"] for entry in entries)

    first, said, result, last = (entry["message"] for entry in entries)
    assert first["role"] == "user"
    assert first["content"] == [{"type": "text", "text": history[0].user}]
    assert said["role"] == "assistant"
    text, call = said["content"]
    assert text == {"type": "text", "text": "Searching for the old client."}
    assert call["type"] == "toolCall"
    assert call["name"] == "bash"
    assert call["arguments"] == {"command": 'grep -rn "BillingClient(" src'}
    assert said["stopReason"] == "toolUse"
    assert {"api", "provider", "model", "usage"} <= said.keys()
    assert result == {
        "role": "toolResult",
        "toolCallId": call["id"],
        "toolName": "bash",
        "content": [
            {"type": "text", "text": "src/invoice.py:12: client = BillingClient()\n"}
        ],
        "isError": False,
        "timestamp": result["timestamp"],
    }
    assert last["content"] == [
        {"type": "text", "text": "One call site, in invoice.py."}
    ]
    assert last["stopReason"] == "stop"


def test_pi_plants_a_slash_command_as_the_expanded_skill(tmp_path) -> None:
    """pi's own shape: what it expands /skill:name to, from the installed copy."""
    harness = PiHarness()
    refs = _install(harness, tmp_path, "repro-first")
    history = [
        PlantedTurn(user="/repro-first the double dash"),
        PlantedTurn(assistant="On it."),
    ]

    _, lines = _plant(tmp_path, harness, PI_SESSION, history, refs)

    [typed] = lines[1]["message"]["content"]
    installed = tmp_path / "home" / ".pi" / "agent" / "skills" / "repro-first"
    assert typed["text"] == (
        f'<skill name="repro-first" location="{installed / "SKILL.md"}">\n'
        f"References are relative to {installed}.\n\n"
        "Installed copy.\n</skill>\n\nthe double dash"
    )


# --- each backend resumes what it planted ----------------------------------------

_TWO_TURNS = [PlantedTurn(user="hi"), PlantedTurn(assistant="hello")]


def _spawn(monkeypatch, tmp_path, harness, history, version=None):
    """Run ``harness`` once over ``history``; the argv and stdin it spawned.

    ``version`` is what the discovered CLI answers ``--version`` with; without
    one, the agent is claude-code's bare ``claude``.
    """

    seen: dict = {}

    def fake_run(cmd, **kwargs):
        if cmd[1:] == ["--version"]:
            return subprocess.CompletedProcess(cmd, 0, stdout=version or "")
        if version or cmd[0] == "claude":
            seen["cmd"] = cmd
            seen["input"], seen["stdin"] = kwargs.get("input"), kwargs.get("stdin")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="")

    patch_cli_calls(monkeypatch, fake_run)
    (tmp_path / "home").mkdir(parents=True)
    (tmp_path / "work").mkdir()
    harness.run(
        run_context(
            prompt="Finish it.",
            isolated_home=str(tmp_path / "home"),
            workdir=str(tmp_path / "work"),
            history=history,
        )
    )
    return seen


def _codex_spawn(monkeypatch, tmp_path, history):
    monkeypatch.setattr("caliper.harness.base.shutil.which", lambda name: "codex")
    monkeypatch.setattr("caliper.harness.codex.CODEX_APP_CLI", tmp_path / "missing")
    return _spawn(monkeypatch, tmp_path, CodexHarness(), history, "codex-cli 0.162.0\n")


def _pi_spawn(monkeypatch, tmp_path, history):
    monkeypatch.setattr("caliper.harness.base.shutil.which", lambda _n, **_: "pi")
    return _spawn(monkeypatch, tmp_path, PiHarness(), history, "0.87.1\n")


def test_claude_code_resumes_the_session_it_planted(monkeypatch, tmp_path) -> None:
    seen = _spawn(monkeypatch, tmp_path, ClaudeCodeHarness(), _TWO_TURNS)

    cmd = seen["cmd"]
    assert cmd[-1] == "Finish it."
    session_id = cmd[cmd.index("--resume") + 1]
    projects = tmp_path / "home" / ".claude" / "projects"
    assert len(list(projects.glob(f"*/{session_id}.jsonl"))) == 1


def test_claude_code_starts_fresh_without_a_history(monkeypatch, tmp_path) -> None:
    seen = _spawn(monkeypatch, tmp_path, ClaudeCodeHarness(), None)

    assert "--resume" not in seen["cmd"]
    assert not (tmp_path / "home" / ".claude" / "projects").exists()


def test_codex_resumes_the_rollout_it_planted(monkeypatch, tmp_path) -> None:
    seen = _codex_spawn(monkeypatch, tmp_path, _TWO_TURNS)

    # The exec flags stay ahead of the subcommand; the prompt is still stdin.
    cmd = seen["cmd"]
    assert cmd[1] == "exec"
    assert cmd[-3] == "resume"
    assert cmd[-1] == "-"
    assert cmd.index("--json") < cmd.index("resume")
    assert seen["input"] == "Finish it."
    sessions = tmp_path / "home" / ".codex" / "sessions"
    assert len(list(sessions.glob(f"*/*/*/rollout-*-{cmd[-2]}.jsonl"))) == 1


def test_codex_starts_fresh_without_a_history(monkeypatch, tmp_path) -> None:
    seen = _codex_spawn(monkeypatch, tmp_path, None)

    assert "resume" not in seen["cmd"]
    assert not (tmp_path / "home" / ".codex" / "sessions").exists()


def test_pi_resumes_the_session_it_planted(monkeypatch, tmp_path) -> None:
    seen = _pi_spawn(monkeypatch, tmp_path, _TWO_TURNS)

    cmd = seen["cmd"]
    assert "--no-session" not in cmd
    assert Path(cmd[cmd.index("--session") + 1]).is_file()
    assert cmd[-1] == "Finish it."
    # Left open, stdin hangs pi after it loads the session (pi 0.87.1).
    assert seen["stdin"] is subprocess.DEVNULL


def test_pi_starts_fresh_without_a_history(monkeypatch, tmp_path) -> None:
    seen = _pi_spawn(monkeypatch, tmp_path, None)

    assert "--no-session" in seen["cmd"]
    assert "--session" not in seen["cmd"]
    assert not (tmp_path / "home" / ".pi" / "agent" / "sessions").exists()


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

    assert "<prior_context>" not in render_judge_prompt("x", "p", [])


def test_the_judge_gets_the_turns_the_attempt_resumed_from(tmp_path) -> None:
    """Read once per run: an edit mid-run can't split the agent from its judge."""

    spec_path = _spec(tmp_path)
    harness = ScriptedHarness(supports_history=True)
    seen: list = []

    class Judge:
        backend, model, prompt_version = "test", None, None

        def cli_version(self):
            return None

        def evaluate(self, task, transcript, workdir, history=None):
            seen.append(history)
            (tmp_path / "h.history.yaml").write_text("- user: edited\n")
            return JudgeResult(passed=True)

    run(load_spec(spec_path), spec_path, harness, Judge(), k=2, workers=1)

    planted = [ctx.history for ctx in harness.contexts]
    assert seen == planted
    assert planted[0] == planted[1]
    assert planted[0][0].user.startswith("Migrate billing")
