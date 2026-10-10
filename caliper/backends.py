"""Every backend caliper knows, and the facts about each that live outside its harness.

The one table that ``--model``, ``get_harness``, ``update-cli`` and the reporter
read, so adding a backend is one entry here plus its harness class. What a
backend *does* stays in its harness (docs/adr/0020); this holds only what the
rest of caliper must know without building one. Stdlib-only, so the spec schema
can import it without pulling in the harnesses.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Npm:
    """A CLI `caliper update-cli` updates with ``npm install -g``."""

    package: str


@dataclass(frozen=True)
class SelfUpdate:
    """A CLI that updates itself, so `caliper update-cli` points at its command."""

    command: str


@dataclass(frozen=True)
class Backend:
    name: str
    # ``module:Class``, imported only when the backend is used. A string rather
    # than an import because only caliper.harness may import a backend module
    # (the TID251 banned-api rule in pyproject.toml).
    harness: str
    updater: Npm | SelfUpdate
    # How the agent spells an MCP tool: ``mcp<sep><server><sep><tool>``
    # (docs/CONTEXT.md → MCP server (declared)).
    mcp_tool_separator: str = "__"


BACKENDS: dict[str, Backend] = {
    b.name: b
    for b in (
        Backend(
            "claude-code",
            "caliper.harness.claude_code:ClaudeCodeHarness",
            Npm("@anthropic-ai/claude-code"),
        ),
        Backend(
            "codex",
            "caliper.harness.codex:CodexHarness",
            Npm("@openai/codex"),
        ),
        Backend(
            "hermes",
            "caliper.harness.hermes:HermesHarness",
            SelfUpdate("hermes update"),
            mcp_tool_separator="_",
        ),
        Backend(
            "pi",
            "caliper.harness.pi:PiHarness",
            Npm("@earendil-works/pi-coding-agent"),
        ),
    )
}

VALID_BACKENDS: frozenset[str] = frozenset(BACKENDS)

# The engine (backend + model) is a runtime axis, not a spec field: it is chosen
# at invocation via --model / --judge-model. The skill defaults to this, and the
# judge to the skill's backend (docs/adr/0034). A saved run still records the
# actual engine in RunMeta, so de-pinning costs no reproducibility. See
# docs/adr/0004-engine-is-a-runtime-axis-not-a-spec-field.md.
DEFAULT_BACKEND: str = "claude-code"

_ALIASES = {"claude": "claude-code", "claude_code": "claude-code"}


def normalize_backend(value: str) -> str:
    """The registered name for ``value``, or ``value`` unchanged if it is none."""
    return _ALIASES.get(value, value)


def mcp_tool_separator(backend: str) -> str:
    """The separator ``backend`` writes in MCP tool names.

    A run saved by a backend no longer registered reads as the common ``__``.
    """
    entry = BACKENDS.get(normalize_backend(backend))
    return entry.mcp_tool_separator if entry else "__"
