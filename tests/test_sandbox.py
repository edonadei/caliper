from __future__ import annotations

from pathlib import Path

from caliper.harness.base import ConversationTurn
from caliper.sandbox import SpecSandbox
from caliper.schema.spec import EvalSpec, SandboxConfig, TaskSpec


def _spec(forbidden: list[str] | None = None) -> EvalSpec:
    return EvalSpec(
        skills=[],
        tasks=[TaskSpec(id="task-001", name="t", prompt="go", expect="it works")],
        sandbox=SandboxConfig(forbidden_files=forbidden or []),
    )


def _read(path: str) -> ConversationTurn:
    return ConversationTurn(
        role="tool_use", content="", tool_name="Read", tool_input={"file_path": path}
    )


def test_declared_pattern_is_a_violation() -> None:
    sandbox = SpecSandbox(declared=[r".*answers\.txt$"])
    assert sandbox.violations([_read("/work/answers.txt")]) == ["/work/answers.txt"]


def test_unmatched_path_is_not_a_violation() -> None:
    sandbox = SpecSandbox(declared=[r".*answers\.txt$"])
    assert sandbox.violations([_read("/work/notes.md")]) == []


def test_turn_without_tool_input_is_ignored() -> None:
    sandbox = SpecSandbox(declared=[r".*"])
    turn = ConversationTurn(role="assistant", content="/work/answers.txt")
    assert sandbox.violations([turn]) == []


def test_nested_tool_input_is_searched() -> None:
    sandbox = SpecSandbox(declared=[r"answers\.txt"])
    turn = ConversationTurn(
        role="tool_use",
        content="",
        tool_name="Bash",
        tool_input={"args": {"paths": ["/work/answers.txt"]}},
    )
    assert sandbox.violations([turn]) == ["/work/answers.txt"]


def test_deeply_buried_path_is_not_searched() -> None:
    """The scan stops at depth 5 — an unbounded walk on agent-supplied input."""
    sandbox = SpecSandbox(declared=[r"answers\.txt"])
    buried: dict = {"a": {"b": {"c": {"d": {"e": {"f": "/work/answers.txt"}}}}}}
    turn = ConversationTurn(
        role="tool_use", content="", tool_name="Bash", tool_input=buried
    )
    assert sandbox.violations([turn]) == []


def test_non_path_strings_are_not_candidates() -> None:
    sandbox = SpecSandbox(declared=[r"answers"])
    turn = ConversationTurn(
        role="tool_use", content="", tool_name="Bash", tool_input={"note": "answers"}
    )
    assert sandbox.violations([turn]) == []


def test_from_spec_forbids_the_spec_file_itself(tmp_path: Path) -> None:
    """Reading the spec is reading the answer key, whatever the spec declared."""
    spec_file = tmp_path / "demo.eval.yaml"
    spec_file.write_text("name: demo\n")
    sandbox = SpecSandbox.from_spec(_spec(), spec_file)

    assert sandbox.violations([_read(str(spec_file))]) == [str(spec_file)]


def test_from_spec_forbids_calipers_own_directory(tmp_path: Path) -> None:
    """Last run's saved results are an answer key too."""
    saved = tmp_path / ".caliper" / "results" / "demo" / "2026-01-01T00-00-00.json"
    sandbox = SpecSandbox.from_spec(_spec(), tmp_path / "demo.eval.yaml")

    assert sandbox.violations([_read(str(saved))]) == [str(saved)]


def test_from_spec_keeps_the_declared_patterns(tmp_path: Path) -> None:
    sandbox = SpecSandbox.from_spec(
        _spec([r".*answers\.txt$"]), tmp_path / "demo.eval.yaml"
    )

    assert sandbox.violations([_read("/work/answers.txt")]) == ["/work/answers.txt"]


def test_auto_forbidden_paths_are_literal_not_patterns(tmp_path: Path) -> None:
    """A spec path with regex metacharacters matches itself, not a pattern."""
    spec_file = tmp_path / "demo+v2.eval.yaml"
    spec_file.write_text("name: demo\n")
    sandbox = SpecSandbox.from_spec(_spec(), spec_file)

    assert sandbox.violations([_read(str(spec_file))]) == [str(spec_file)]


def test_install_permits_an_ordinary_skill_file() -> None:
    sandbox = SpecSandbox(declared=[r".*answers\.txt$"])
    assert sandbox.permits_install("SKILL.md")


def test_install_refuses_a_declared_cheat_surface() -> None:
    sandbox = SpecSandbox(declared=[r".*answers\.txt$"])
    assert not sandbox.permits_install("src/answers.txt")


def test_install_matches_a_dot_slash_relative_pattern() -> None:
    """`./src/answers.txt` is how a spec author writes a repo-relative path."""
    sandbox = SpecSandbox(declared=[r"^\./src/answers\.txt$"])
    assert not sandbox.permits_install("src/answers.txt")


def test_install_ignores_the_auto_forbidden_paths(tmp_path: Path) -> None:
    """Auto-forbidden paths are absolute host paths, outside any skill directory.

    Applying them to a skill's *relative* install paths could only ever match by
    accident, so the install filters on the declared patterns alone.
    """
    spec_file = tmp_path / "demo.eval.yaml"
    spec_file.write_text("name: demo\n")
    sandbox = SpecSandbox.from_spec(_spec(), spec_file)

    assert sandbox.permits_install("demo.eval.yaml")
