from __future__ import annotations

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from caliper.main import app
from caliper.schema.spec import load_spec


def _write(tmp_path, text: str):
    p = tmp_path / "s.eval.yaml"
    p.write_text(text)
    return p


_TASK = "tasks:\n  - name: t\n    prompt: p\n    assert: assert True\n"


def test_load_spec_accepts_a_skill_neighbourhood(tmp_path) -> None:
    spec = load_spec(
        _write(
            tmp_path,
            "skills:\n  - ./SKILL.md\n  - ../evaluate-skill/SKILL.md\n" + _TASK,
        )
    )
    assert spec.skills == ["./SKILL.md", "../evaluate-skill/SKILL.md"]
    assert spec.tasks[0].id == "task-001"


def test_load_spec_accepts_bare_agent_with_no_skills_key(tmp_path) -> None:
    assert load_spec(_write(tmp_path, _TASK)).skills == []


def test_load_spec_rejects_the_old_singular_skill_key(tmp_path) -> None:
    with pytest.raises(ValueError) as exc:
        load_spec(_write(tmp_path, "skill:\n  path: ./SKILL.md\n" + _TASK))
    msg = str(exc.value)
    assert "skills:" in msg
    # The error teaches the new shape (a list of paths), not just "unknown key".
    assert "- ./SKILL.md" in msg


@pytest.mark.parametrize(
    "removed, needle",
    [
        ("skill:\n  path: ./SKILL.md\n  backend: codex\n", "skill.backend"),
        ("skill:\n  path: ./SKILL.md\n  model: claude-sonnet-4-6\n", "skill.model"),
        ("skills:\n  - ./SKILL.md\njudge:\n  backend: codex\n", "judge"),
    ],
)
def test_load_spec_rejects_removed_engine_keys(tmp_path, removed, needle) -> None:
    with pytest.raises(ValueError) as exc:
        load_spec(_write(tmp_path, removed + _TASK))
    msg = str(exc.value)
    assert needle in msg
    # The error must point users at the runtime flags, not just say "unknown key".
    assert "--model" in msg or "--judge-model" in msg


# --- activates: as a third check type -------------------------------------


def test_activates_alone_satisfies_the_at_least_one_check_rule(tmp_path) -> None:
    spec = load_spec(
        _write(
            tmp_path,
            "skills:\n  - ./SKILL.md\n"
            "tasks:\n  - name: t\n    prompt: p\n    activates: [my-skill]\n",
        )
    )
    assert spec.tasks[0].activates == ["my-skill"]


def test_empty_activates_asserts_silence_and_is_a_real_check(tmp_path) -> None:
    # `activates: []` is falsy but is an assertion — "nothing should fire".
    spec = load_spec(
        _write(
            tmp_path,
            "skills:\n  - ./SKILL.md\n"
            "tasks:\n  - name: t\n    prompt: p\n    activates: []\n",
        )
    )
    assert spec.tasks[0].activates == []


def test_task_with_no_check_at_all_is_rejected(tmp_path) -> None:
    with pytest.raises(ValueError) as exc:
        load_spec(_write(tmp_path, "tasks:\n  - name: t\n    prompt: p\n"))
    assert "activates" in str(exc.value)


def test_task_may_carry_both_expect_and_activates(tmp_path) -> None:
    spec = load_spec(
        _write(
            tmp_path,
            "skills:\n  - ./SKILL.md\n"
            "tasks:\n  - name: t\n    prompt: p\n"
            "    expect: It works.\n    activates: [my-skill]\n",
        )
    )
    task = spec.tasks[0]
    assert task.expect == "It works."
    assert task.activates == ["my-skill"]


def test_activates_defaults_to_none_meaning_not_asserted(tmp_path) -> None:
    spec = load_spec(_write(tmp_path, "skills:\n  - ./SKILL.md\n" + _TASK))
    assert spec.tasks[0].activates is None


# --- errors that would otherwise surface only after a paid run (#138) -------


def test_invalid_forbidden_files_regex_is_rejected_with_its_index(tmp_path) -> None:
    with pytest.raises(ValidationError) as exc:
        load_spec(
            _write(
                tmp_path,
                "sandbox:\n  forbidden_files:\n    - ok\\.txt\n    - '[unclosed'\n"
                + _TASK,
            )
        )
    msg = str(exc.value)
    assert "forbidden_files" in msg
    assert "[1]" in msg
    assert "[unclosed" in msg


def test_missing_assert_script_is_rejected(tmp_path) -> None:
    with pytest.raises(ValidationError) as exc:
        load_spec(
            _write(
                tmp_path, "tasks:\n  - name: t\n    prompt: p\n    assert: ./nope.py\n"
            )
        )
    assert "nope.py" in str(exc.value)


def test_assert_script_resolves_against_the_spec_dir(tmp_path, monkeypatch) -> None:
    (tmp_path / "check.py").write_text("assert True\n")
    # Launched from elsewhere: the path must resolve beside the spec, not the cwd.
    monkeypatch.chdir(tmp_path.parent)
    spec = load_spec(
        _write(tmp_path, "tasks:\n  - name: t\n    prompt: p\n    assert: ./check.py\n")
    )
    assert spec.tasks[0].assert_script == "./check.py"


def test_inline_assert_code_is_not_mistaken_for_a_path(tmp_path) -> None:
    spec = load_spec(
        _write(
            tmp_path,
            "tasks:\n  - name: t\n    prompt: p\n    assert: |\n"
            "      import os\n      assert os.path.exists('x.py')\n",
        )
    )
    assert spec.tasks[0].assert_script


@pytest.mark.parametrize("typo", ["asert: assert True", "activate: [my-skill]"])
def test_unknown_task_key_is_rejected(tmp_path, typo) -> None:
    with pytest.raises(ValidationError) as exc:
        load_spec(
            _write(
                tmp_path,
                f"tasks:\n  - name: t\n    prompt: p\n    expect: ok\n    {typo}\n",
            )
        )
    assert typo.split(":")[0] in str(exc.value)


def test_unknown_sandbox_key_is_rejected(tmp_path) -> None:
    with pytest.raises(ValidationError) as exc:
        load_spec(_write(tmp_path, "sandbox:\n  forbiden_files: ['x']\n" + _TASK))
    assert "forbiden_files" in str(exc.value)


_BROKEN_SPECS = {
    "bad-regex": "sandbox:\n  forbidden_files: ['[unclosed']\n" + _TASK,
    "missing-assert": "tasks:\n  - name: t\n    prompt: p\n    assert: ./nope.py\n",
    "unknown-sandbox-key": "sandbox:\n  forbiden_files: ['x']\n" + _TASK,
    "unknown-key": "tasks:\n  - name: t\n    prompt: p\n    expect: ok\n    asert: x\n",
}


@pytest.mark.parametrize("case", sorted(_BROKEN_SPECS))
def test_validate_rejects_the_spec(tmp_path, case) -> None:
    result = CliRunner().invoke(
        app, ["validate", str(_write(tmp_path, _BROKEN_SPECS[case]))]
    )
    assert result.exit_code != 0
    assert "Spec is valid" not in result.output


@pytest.mark.parametrize("case", sorted(_BROKEN_SPECS))
def test_run_rejects_the_spec_before_its_first_attempt(
    tmp_path, monkeypatch, case
) -> None:
    def no_attempt(*args, **kwargs):
        raise AssertionError("run reached the harness with a broken spec")

    monkeypatch.setattr("caliper.commands.run.get_harness", no_attempt)
    result = CliRunner().invoke(
        app, ["run", str(_write(tmp_path, _BROKEN_SPECS[case])), "--k", "1"]
    )
    assert result.exit_code != 0
    assert result.exception is None or isinstance(result.exception, SystemExit)


@pytest.mark.parametrize(
    "text, message",
    [
        ("", "the spec is empty"),
        ("- a\n", "must be a mapping with a `tasks:` list"),
    ],
)
def test_a_spec_that_is_not_a_mapping_says_so(tmp_path, text, message) -> None:
    with pytest.raises(ValueError, match=message):
        load_spec(_write(tmp_path, text))


@pytest.mark.parametrize(
    "text, message",
    [
        ("tasks:\n", "`tasks:` must be a list of tasks, not nothing"),
        ("tasks: go\n", "`tasks:` must be a list of tasks, not a string"),
        ("tasks: [foo]\n", "task 1 must be a mapping with `name:` and `prompt:`"),
    ],
)
def test_a_malformed_tasks_list_says_what_shape_it_needs(
    tmp_path, text, message
) -> None:
    with pytest.raises(ValueError, match=message):
        load_spec(_write(tmp_path, text))


def test_validate_says_when_a_spec_loads_user_customizations(tmp_path) -> None:
    result = CliRunner().invoke(
        app,
        ["validate", str(_write(tmp_path, "user_customizations: true\n" + _TASK))],
    )
    assert result.exit_code == 0, result.output
    assert "--no-user-customizations" in result.output


def test_validate_says_when_a_spec_pins_isolation(tmp_path) -> None:
    result = CliRunner().invoke(
        app, ["validate", str(_write(tmp_path, "user_customizations: false\n" + _TASK))]
    )
    assert result.exit_code == 0, result.output
    assert "user_customizations: false" in result.output


def test_validate_is_silent_about_user_customizations_by_default(tmp_path) -> None:
    result = CliRunner().invoke(app, ["validate", str(_write(tmp_path, _TASK))])
    assert result.exit_code == 0, result.output
    assert "--user-customizations" not in result.output


def test_every_smoke_eval_pins_isolation() -> None:
    # Runs inherit the machine's MCP setup by default (docs/adr/0028); a smoke
    # eval measures the backend, and its probe tasks assert zero undeclared MCP
    # tools, so each one must opt out explicitly.
    from pathlib import Path

    smoke = sorted(Path(__file__).parent.glob("*-smoke.eval.yaml"))
    assert smoke
    for path in smoke:
        assert load_spec(path).user_customizations is False, path.name
