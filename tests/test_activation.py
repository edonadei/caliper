from __future__ import annotations

from caliper.activation import ActivationDetector, check_activation
from caliper.harness.base import ConversationTurn


def tool(name: str, tool_input: dict) -> ConversationTurn:
    return ConversationTurn(
        role="tool_use",
        content=f"[tool: {name}]",
        tool_name=name,
        tool_input=tool_input,
    )


# --- dedicated tool shape (claude-code, hermes) ---------------------------


def test_detects_claude_codes_skill_tool_call():
    detector = ActivationDetector(["unit-normalizer"], frozenset({"Skill"}))
    turns = [tool("Skill", {"skill": "unit-normalizer", "args": ""})]
    assert detector.detect(turns) == ["unit-normalizer"]


def test_detects_hermes_skill_view_call():
    detector = ActivationDetector(["unit-normalizer"], frozenset({"skill_view"}))
    turns = [tool("skill_view", {"name": "unit-normalizer"})]
    assert detector.detect(turns) == ["unit-normalizer"]


def test_dedicated_tool_naming_an_undeclared_skill_is_ignored():
    # The neighbourhood is closed; anything else is noise, not an observation.
    detector = ActivationDetector(["mine"], frozenset({"Skill"}))
    assert detector.detect([tool("Skill", {"skill": "something-else"})]) == []


# --- path shape (codex, pi) -----------------------------------------------


def test_detects_an_absolute_path_read():
    detector = ActivationDetector(["unit-normalizer"], frozenset())
    turns = [
        tool("read", {"path": "/tmp/caliper-x/.codex/skills/unit-normalizer/SKILL.md"})
    ]
    assert detector.detect(turns) == ["unit-normalizer"]


def test_detects_the_same_activation_written_as_a_relative_path():
    # The spike saw codex emit one run absolute and the next relative; matching
    # on the full path would have missed the second.
    detector = ActivationDetector(["unit-normalizer"], frozenset())
    turns = [
        tool(
            "shell",
            {"command": "sed -n '1,240p' .codex/skills/unit-normalizer/SKILL.md"},
        )
    ]
    assert detector.detect(turns) == ["unit-normalizer"]


def test_listing_the_skills_root_is_not_activation():
    detector = ActivationDetector(["unit-normalizer"], frozenset())
    assert detector.detect([tool("shell", {"command": "ls .codex/skills/"})]) == []


def test_a_similarly_named_skill_does_not_match():
    detector = ActivationDetector(["normalizer"], frozenset())
    turns = [tool("read", {"path": "/x/skills/unit-normalizer/SKILL.md"})]
    assert detector.detect(turns) == []


# --- the union rule -------------------------------------------------------


def test_a_direct_file_read_counts_even_where_a_dedicated_tool_exists():
    # "Preferring" the dedicated tool would make claude-code the one backend
    # able to report a false negative. An agent that read the installed file has
    # demonstrably brought the skill into context.
    detector = ActivationDetector(["mine"], frozenset({"Skill"}))
    turns = [tool("Read", {"file_path": "/h/.claude/skills/mine/SKILL.md"})]
    assert detector.detect(turns) == ["mine"]


def test_result_is_a_deduplicated_sorted_set():
    detector = ActivationDetector(["b-skill", "a-skill"], frozenset({"Skill"}))
    turns = [
        tool("Skill", {"skill": "b-skill"}),
        tool("Read", {"file_path": "/h/skills/b-skill/SKILL.md"}),
        tool("Skill", {"skill": "a-skill"}),
    ]
    assert detector.detect(turns) == ["a-skill", "b-skill"]


def test_no_tool_calls_means_nothing_activated():
    detector = ActivationDetector(["mine"], frozenset({"Skill"}))
    turns = [ConversationTurn(role="assistant", content="I'll just do it myself.")]
    assert detector.detect(turns) == []


def test_detection_is_unavailable_when_no_skills_are_declared():
    # A bare-agent or --baseline attempt installs nothing, so there is nothing to
    # observe — that is "not observed", not "nothing fired".
    assert ActivationDetector([], frozenset({"Skill"})).detect([]) is None


# --- check_activation (exact set match) -----------------------------------


def test_exact_match_passes():
    assert check_activation(["a"], ["a"]) is True


def test_a_missing_expected_skill_fails():
    assert check_activation([], ["a"]) is False


def test_an_extra_unexpected_skill_fails():
    assert check_activation(["a", "b"], ["a"]) is False


def test_order_does_not_matter():
    assert check_activation(["b", "a"], ["a", "b"]) is True


def test_expected_silence_holds_when_nothing_fired():
    assert check_activation([], []) is True


def test_expected_silence_fails_when_something_fired():
    assert check_activation(["a"], []) is False


def test_not_asserted_yields_no_verdict():
    assert check_activation(["a"], None) is None


def test_unobserved_activation_yields_no_verdict():
    # Nothing was seen, so there is nothing to judge — never a silent failure.
    assert check_activation(None, ["a"]) is None
