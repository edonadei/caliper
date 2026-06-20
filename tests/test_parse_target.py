from __future__ import annotations

import pytest

from caliper.schema.spec import parse_target


@pytest.mark.parametrize(
    "value, expected",
    [
        # backend:model compound
        ("claude-api:claude-sonnet-4-6", ("claude-api", "claude-sonnet-4-6")),
        ("openai-api:gpt-4o", ("openai-api", "gpt-4o")),
        # aliases normalised on the backend side
        ("anthropic:claude-sonnet-4-6", ("claude-api", "claude-sonnet-4-6")),
        ("claude:claude-sonnet-4-6", ("claude-code", "claude-sonnet-4-6")),
        # backend only (known name, no colon)
        ("codex", ("codex", None)),
        ("claude-code", ("claude-code", None)),
        ("claude-api", ("claude-api", None)),
        ("openai-api", ("openai-api", None)),
        # alias as backend-only
        ("anthropic", ("claude-api", None)),
        # plain model name (not a known backend)
        ("claude-sonnet-4-6", (None, "claude-sonnet-4-6")),
        ("gpt-4o", (None, "gpt-4o")),
        # colon with no model → backend only
        ("codex:", ("codex", None)),
        # colon with no backend → model only
        (":claude-sonnet-4-6", (None, "claude-sonnet-4-6")),
        # multiple colons → only first split
        ("claude-api:some:model", ("claude-api", "some:model")),
    ],
)
def test_parse_target(value: str, expected: tuple[str | None, str | None]) -> None:
    assert parse_target(value) == expected
