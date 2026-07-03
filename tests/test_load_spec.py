from __future__ import annotations

import pytest

from caliper.schema.spec import load_spec


def _write(tmp_path, text: str):
    p = tmp_path / "s.eval.yaml"
    p.write_text(text)
    return p


_TASK = "tasks:\n  - name: t\n    prompt: p\n    assert: assert True\n"


def test_load_spec_accepts_engineless_spec(tmp_path) -> None:
    spec = load_spec(_write(tmp_path, "skill:\n  path: ./SKILL.md\n" + _TASK))
    assert spec.skill.path == "./SKILL.md"
    assert spec.tasks[0].id == "task-001"


def test_load_spec_accepts_bare_agent(tmp_path) -> None:
    spec = load_spec(_write(tmp_path, "skill: {}\n" + _TASK))
    assert spec.skill.path is None


@pytest.mark.parametrize(
    "removed, needle",
    [
        ("skill:\n  path: ./SKILL.md\n  backend: codex\n", "skill.backend"),
        ("skill:\n  path: ./SKILL.md\n  model: claude-sonnet-4-6\n", "skill.model"),
        ("skill:\n  path: ./SKILL.md\njudge:\n  backend: codex\n", "judge"),
    ],
)
def test_load_spec_rejects_removed_engine_keys(tmp_path, removed, needle) -> None:
    with pytest.raises(ValueError) as exc:
        load_spec(_write(tmp_path, removed + _TASK))
    msg = str(exc.value)
    assert needle in msg
    # The error must point users at the runtime flags, not just say "unknown key".
    assert "--model" in msg or "--judge-model" in msg
