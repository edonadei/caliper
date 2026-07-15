from __future__ import annotations

from caliper.schema.spec import (
    DEFAULT_JUDGE_MODEL,
    resolve_judge_model,
)


def test_resolve_judge_model_pins_claude_code_default() -> None:
    assert resolve_judge_model("claude-code", None) == DEFAULT_JUDGE_MODEL
    assert resolve_judge_model("claude", None) == DEFAULT_JUDGE_MODEL


def test_resolve_judge_model_preserves_explicit_model() -> None:
    assert resolve_judge_model("claude-code", "claude-opus-4-8") == "claude-opus-4-8"


def test_resolve_judge_model_leaves_other_backends_unpinned() -> None:
    assert resolve_judge_model("codex", None) is None
    assert resolve_judge_model("pi", None) is None
    assert resolve_judge_model("hermes", None) is None
