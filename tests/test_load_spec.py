from __future__ import annotations

import pytest

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
