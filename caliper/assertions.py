"""Helpers for ``assert:`` scripts that check *how* the agent worked.

An ``assert:`` runs with caliper's own interpreter, so it can import these::

    from caliper.assertions import tool_calls

    assert tool_calls("Bash", match=r"pytest"), "never ran the tests"
    assert not tool_calls("Write", match=r"\\.env$"), "wrote a secrets file"

They read the file ``CALIPER_TRANSCRIPT`` names: a JSON list of turns shaped
like ``AttemptRecord.transcript`` in the results JSON. Tool names are the
backend's own (``Bash`` on claude-code, ``shell`` on codex, …).
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from caliper.workdir import TRANSCRIPT_ENV


def transcript() -> list[dict[str, Any]]:
    """Every turn of the attempt, in order."""
    path = os.environ.get(TRANSCRIPT_ENV)
    if not path:
        raise RuntimeError(f"{TRANSCRIPT_ENV} is not set: run this from an assert:")
    return json.loads(Path(path).read_text(encoding="utf-8"))


def tool_calls(name: str | None = None, match: str | None = None) -> list[dict]:
    """The agent's tool calls, in order, optionally filtered.

    ``name`` keeps calls to that tool. ``match`` is a regex searched in each
    string in the call's input, so ``match=r"pytest"`` finds a shell call whose
    command mentions it wherever the backend puts the command.
    """
    pattern = re.compile(match) if match is not None else None
    return [
        turn
        for turn in transcript()
        if turn["role"] == "tool_use"
        and (name is None or turn.get("tool_name") == name)
        and (
            pattern is None
            or any(pattern.search(s) for s in _strings(turn.get("tool_input")))
        )
    ]


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        value = list(value.values())
    if isinstance(value, list):
        return [s for item in value for s in _strings(item)]
    return []
