from __future__ import annotations

import pytest

from caliper.schema.spec import parse_target


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        # backend:model compound
        ("codex:gpt-5-codex", ("codex", "gpt-5-codex")),
        ("pi:claude-sonnet-4-6", ("pi", "claude-sonnet-4-6")),
        # hermes takes a provider/model value straight through to its -m flag
        (
            "hermes:anthropic/claude-sonnet-4.6",
            ("hermes", "anthropic/claude-sonnet-4.6"),
        ),
        ("hermes", ("hermes", None)),
        # backend only (known name, no colon)
        ("codex", ("codex", None)),
        ("claude-code", ("claude-code", None)),
        ("pi", ("pi", None)),
        # plain model name (not a known backend)
        ("claude-sonnet-4-6", (None, "claude-sonnet-4-6")),
        ("gpt-4o", (None, "gpt-4o")),
        # colon with no model → backend only
        ("codex:", ("codex", None)),
        # colon with no backend → model only
        (":claude-sonnet-4-6", (None, "claude-sonnet-4-6")),
        # multiple colons → only first split
        ("codex:some:model", ("codex", "some:model")),
    ],
)
def test_parse_target(value: str, expected: tuple[str | None, str | None]) -> None:
    assert parse_target(value) == expected
