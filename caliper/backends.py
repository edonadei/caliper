"""Every backend caliper knows, and the facts about each that live outside its harness.

The one table that ``--model``, ``get_harness`` and the reporter read, so
adding a backend is one entry here plus its harness class. What a backend
*does* stays in its harness (docs/adr/0020); this holds only what the rest of
caliper must know without building one. Stdlib-only, so the spec schema
can import it without pulling in the harnesses.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Backend:
    name: str
    # ``module:Class``, imported only when the backend is used. A string rather
    # than an import because only caliper.harness may import a backend module
    # (the TID251 banned-api rule in pyproject.toml).
    harness: str
    # How the agent spells an MCP tool: ``mcp<sep><server><sep><tool>``
    # (docs/CONTEXT.md → MCP server (declared)).
    mcp_tool_separator: str = "__"
    # The words this backend's CLI takes after ``@`` in ``--model``, as the
    # current CLI spells them; ``None`` passes any word through for the CLI to
    # judge (docs/adr/0044).
    effort_levels: frozenset[str] | None = None


_LOW_TO_MAX = frozenset({"low", "medium", "high", "xhigh", "max"})
# codex checks its own level, so it declares none; these are the words it takes
# today, which ``--model`` must still recognise as an effort.
_CODEX_LEVELS = frozenset({"none", "minimal", "low", "medium", "high", "xhigh"})


BACKENDS: dict[str, Backend] = {
    b.name: b
    for b in (
        Backend(
            "claude-code",
            "caliper.harness.claude_code:ClaudeCodeHarness",
            effort_levels=_LOW_TO_MAX,
        ),
        Backend(
            "codex",
            "caliper.harness.codex:CodexHarness",
        ),
        Backend(
            "hermes",
            "caliper.harness.hermes:HermesHarness",
            mcp_tool_separator="_",
            effort_levels=frozenset(
                {"none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"}
            ),
        ),
        Backend(
            "pi",
            "caliper.harness.pi:PiHarness",
            effort_levels=frozenset(
                {"off", "minimal", "low", "medium", "high", "xhigh", "max"}
            ),
        ),
        Backend(
            "antigravity",
            "caliper.harness.antigravity:AntigravityHarness",
            mcp_tool_separator="_",
            effort_levels=_LOW_TO_MAX,
        ),
        Backend(
            "opencode",
            "caliper.harness.opencode:OpenCodeHarness",
        ),
    )
}

VALID_BACKENDS: frozenset[str] = frozenset(BACKENDS)

# Every word some backend takes as an effort. ``parse_target`` splits ``@<word>`` off a model only for one
# of these, so a Vertex-style ``claude-sonnet-4@20250514`` keeps its ``@``.
EFFORT_WORDS: frozenset[str] = frozenset().union(
    _CODEX_LEVELS,
    *(b.effort_levels for b in BACKENDS.values() if b.effort_levels),
)

# The engine (backend + model) is a runtime axis, not a spec field: it is chosen
# at invocation via --model / --judge-model. The skill defaults to this, and the
# judge to the skill's backend (docs/adr/0034). A saved run still records the
# actual engine in RunMeta, so de-pinning costs no reproducibility. See
# docs/adr/0004-engine-is-a-runtime-axis-not-a-spec-field.md.
DEFAULT_BACKEND: str = "claude-code"

_ALIASES = {"claude": "claude-code", "claude_code": "claude-code", "agy": "antigravity"}


def normalize_backend(value: str) -> str:
    """The registered name for ``value``, or ``value`` unchanged if it is none."""
    return _ALIASES.get(value, value)


def mcp_tool_separator(backend: str) -> str:
    """The separator ``backend`` writes in MCP tool names.

    A run saved by a backend no longer registered reads as the common ``__``.
    """
    entry = BACKENDS.get(normalize_backend(backend))
    return entry.mcp_tool_separator if entry else "__"


def mcp_tool_name(backend: str, server: str, tool: str) -> str:
    """The name ``backend`` gives a call to ``tool`` on ``server``."""
    sep = mcp_tool_separator(backend)
    return f"mcp{sep}{server}{sep}{tool}"


def mcp_server_of(tool_name: str, servers: list[str], backend: str) -> str | None:
    """The one known server a tool call belongs to, or ``None``.

    The separator is the producing backend's, never guessed from the name: a
    server name may itself contain ``_`` or ``__``, so ``mcp__mail_read`` is
    hermes calling ``_mail``, or claude-code calling ``mail_read``'s server.
    Within one form the longest known server wins: ``mcp_mail_archive_read``
    starts with both ``mail_`` and ``mail_archive_``.
    """
    matches = [
        s for s in servers if tool_name.startswith(mcp_tool_name(backend, s, ""))
    ]
    return max(matches, key=len) if matches else None
