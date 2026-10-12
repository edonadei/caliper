"""One place spells an MCP tool name and reads the server back out of it."""

from __future__ import annotations

import pytest

from caliper.backends import mcp_server_of, mcp_tool_name


@pytest.mark.parametrize(
    ("backend", "expected"),
    [
        ("claude-code", "mcp__mail__read"),
        ("codex", "mcp__mail__read"),
        ("hermes", "mcp_mail_read"),
    ],
)
def test_a_spelled_name_reads_back_to_its_server(backend, expected) -> None:
    name = mcp_tool_name(backend, "mail", "read")

    assert name == expected
    assert mcp_server_of(name, ["mail", "other"], backend) == "mail"
