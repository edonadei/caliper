"""Observing which skills the agent chose to bring into context.

One detector, four backends. Each backend contributes *facts* about itself — a
skills root it installs into, and the name of its dedicated skill tool if it has
one — while the matching rule lives here, mirroring how ``_CheatDetector``
inspects the same normalized ``ConversationTurn`` stream (docs/adr/0014).

Two observable shapes exist and the rule is their **union**:

- a **dedicated tool call that names the skill** — claude-code's ``Skill``,
  hermes' ``skill_view``;
- a **plain tool call whose path identifies it** — codex and pi just read the
  file.

The union matters even on a backend that has a dedicated tool: an agent that
reads the installed ``SKILL.md`` directly has demonstrably brought the skill into
context, and "preferring" the tool would make that backend the only one able to
report a false negative.

Matching is on the ``<name>/SKILL.md`` **suffix**, never a full path: the spike
saw codex emit the same activation as an absolute path in one run and a relative
one in the next.
"""

from __future__ import annotations

import re

from caliper.harness.base import ConversationTurn

# Keys a dedicated skill tool uses to name its skill, across backends.
_SKILL_NAME_KEYS = ("skill", "name", "skill_name")
_MAX_DEPTH = 5


class ActivationDetector:
    """Reads an attempt's transcript for the skills the agent reached for."""

    def __init__(self, names: list[str], tool_names: frozenset[str]) -> None:
        self._names = list(names)
        self._tool_names = tool_names
        # `<name>/SKILL.md` at a path boundary — so `unit-normalizer` never
        # matches a read of `some-unit-normalizer/SKILL.md`, and a bare listing
        # of the skills root matches nothing at all.
        self._patterns = {
            name: re.compile(rf"(?:^|[/\s\"']){re.escape(name)}/SKILL\.md\b")
            for name in self._names
        }

    def detect(self, transcript: list[ConversationTurn]) -> list[str] | None:
        """The observed activation set, or ``None`` when nothing was observable.

        ``None`` is reserved for "we could not see" — no skills installed, so no
        choice existed to observe. An empty list is a real observation: the agent
        was offered skills and reached for none.
        """
        if not self._names:
            return None

        found: set[str] = set()
        for turn in transcript:
            if not turn.tool_input:
                continue
            if turn.tool_name in self._tool_names:
                named = self._named_skill(turn.tool_input)
                if named in self._patterns:
                    found.add(named)
            for value in self._strings(turn.tool_input):
                for name, pattern in self._patterns.items():
                    if pattern.search(value):
                        found.add(name)
        return sorted(found)

    def _named_skill(self, tool_input: dict) -> str | None:
        for key in _SKILL_NAME_KEYS:
            value = tool_input.get(key)
            if isinstance(value, str) and value:
                return value
        return None

    def _strings(self, obj: object, depth: int = 0) -> list[str]:
        if depth > _MAX_DEPTH:
            return []
        if isinstance(obj, str):
            return [obj]
        if isinstance(obj, dict):
            out: list[str] = []
            for value in obj.values():
                out.extend(self._strings(value, depth + 1))
            return out
        if isinstance(obj, list):
            out = []
            for item in obj:
                out.extend(self._strings(item, depth + 1))
            return out
        return []


def check_activation(
    observed: list[str] | None, expected: list[str] | None
) -> bool | None:
    """Exact set match, or ``None`` when there is no verdict to give.

    ``None`` covers both "the task asserted nothing" and "nothing was observed" —
    a non-verdict is never silently scored as a failure.
    """
    if expected is None or observed is None:
        return None
    return set(observed) == set(expected)
