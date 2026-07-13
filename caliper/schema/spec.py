from __future__ import annotations

import re
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


_VALID_BACKENDS: frozenset[str] = frozenset({"claude-code", "codex", "pi", "hermes"})

# The engine (backend + model) is a runtime axis, not a spec field: it is chosen
# at invocation via --model / --judge-model and defaults to this. A saved run
# still records the actual engine in RunMeta, so de-pinning costs no
# reproducibility. See docs/adr/0004-engine-is-a-runtime-axis-not-a-spec-field.md.
DEFAULT_BACKEND: str = "claude-code"
# Pinned so the claude-code judge does not inherit a stale model from the
# installed Claude CLI's own default (see issue #59).
DEFAULT_JUDGE_MODEL: str = "claude-sonnet-4-6"


def normalize_backend(value: str) -> str:
    aliases = {
        "claude": "claude-code",
    }
    return aliases.get(value, value)


def resolve_judge_model(backend: str, model: str | None) -> str | None:
    """Return the concrete judge model to pass to the harness.

    When the caller omits ``--judge-model``, only the claude-code judge gets a
    pinned default — other backends keep their CLI-native default.
    """
    if model is not None:
        return model
    if normalize_backend(backend) == "claude-code":
        return DEFAULT_JUDGE_MODEL
    return None


def parse_target(value: str) -> tuple[str | None, str | None]:
    """Parse a --model / --judge-model value into (backend, model).

    Accepts three forms:
      "codex:gpt-5-codex"   -> ("codex", "gpt-5-codex")
      "codex"               -> ("codex", None)   # known backend name
      "claude-sonnet-4-6"   -> (None, "claude-sonnet-4-6")
    """
    if ":" in value:
        backend, _, model = value.partition(":")
        return normalize_backend(backend) or None, model or None
    normalized = normalize_backend(value)
    if normalized in _VALID_BACKENDS:
        return normalized, None
    return None, value


class TaskSpec(BaseModel):
    id: str = ""
    name: str
    prompt: str
    expect: str | None = None
    assert_script: str | None = Field(None, alias="assert")
    setup: str | None = None
    cleanup: str | None = None

    model_config = ConfigDict(populate_by_name=True)

    @model_validator(mode="after")
    def require_at_least_one_check(self) -> "TaskSpec":
        if not self.expect and not self.assert_script:
            raise ValueError(
                f"Task '{self.name}' must have at least one of: expect, assert"
            )
        return self


class SkillConfig(BaseModel):
    # `path` is the only spec-level skill fact; the engine that runs it is a
    # runtime axis (see DEFAULT_BACKEND). Omit `path` to test the bare agent.
    path: str | None = None


class SandboxConfig(BaseModel):
    forbidden_files: list[str] = []
    extra_path: list[str] = []


# Remote transports reach a hosted MCP endpoint over the network; the default
# (``stdio``) spawns a local process.
_REMOTE_MCP_TYPES: frozenset[str] = frozenset({"http", "sse"})
_VALID_MCP_TYPES: frozenset[str] = frozenset({"stdio"}) | _REMOTE_MCP_TYPES


# A declared MCP server the agent-under-test may use. Two transports share one
# model, discriminated by ``type`` (default ``stdio``):
#
#   - stdio (default): a local ``command`` (with ``args``) the harness spawns,
#     plus an ``env`` map. This is the shape from #47.
#   - remote (``type: http`` or ``sse``): a hosted endpoint at ``url`` with an
#     optional ``headers`` map for auth.
#
# The two field sets are mutually exclusive (enforced below). Values in ``env``,
# ``headers``, and a remote ``url`` may reference host env vars as ``${VAR}`` so
# secrets never live in the committed spec — resolved by the backend at run time.
class McpServer(BaseModel):
    type: str = "stdio"
    # stdio transport
    command: str | None = None
    args: list[str] = []
    env: dict[str, str] = {}
    # remote transport (type: http / sse)
    url: str | None = None
    headers: dict[str, str] = {}

    model_config = ConfigDict(extra="forbid")

    @property
    def is_remote(self) -> bool:
        return self.type in _REMOTE_MCP_TYPES

    @model_validator(mode="after")
    def check_transport(self) -> "McpServer":
        if self.type not in _VALID_MCP_TYPES:
            valid = ", ".join(sorted(_VALID_MCP_TYPES))
            raise ValueError(
                f"invalid MCP server type '{self.type}': must be one of {valid}"
            )
        if self.is_remote:
            if not (self.url and self.url.strip()):
                raise ValueError(
                    f"remote MCP server (type: {self.type}) requires a non-empty url"
                )
            stray = [
                field
                for field, present in (
                    ("command", self.command is not None),
                    ("args", bool(self.args)),
                    ("env", bool(self.env)),
                )
                if present
            ]
            if stray:
                raise ValueError(
                    f"remote MCP server (type: {self.type}) cannot set "
                    f"{', '.join(stray)}: those are stdio-only fields"
                )
        else:
            if not (self.command and self.command.strip()):
                raise ValueError("stdio MCP server requires a non-empty command")
            stray = [
                field
                for field, present in (
                    ("url", self.url is not None),
                    ("headers", bool(self.headers)),
                )
                if present
            ]
            if stray:
                raise ValueError(
                    f"stdio MCP server cannot set {', '.join(stray)}: those are "
                    "remote-only (type: http / sse) fields"
                )
        return self


# Server names become the ``mcp__<name>__<tool>`` handle in the transcript, so
# they must be restricted to characters that keep that handle well-formed.
_MCP_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")


class EvalSpec(BaseModel):
    skill: SkillConfig
    sandbox: SandboxConfig = Field(default_factory=SandboxConfig)
    mcp: dict[str, McpServer] = {}
    tasks: list[TaskSpec]

    model_config = ConfigDict(extra="forbid")

    @field_validator("mcp")
    @classmethod
    def validate_server_names(cls, value: dict[str, McpServer]) -> dict[str, McpServer]:
        for name in value:
            if not _MCP_NAME_RE.match(name):
                raise ValueError(
                    f"invalid MCP server name '{name}': names must match "
                    "[A-Za-z0-9_-]+ so the mcp__<name>__<tool> handle is well-formed"
                )
        return value


# Keys removed in ADR 0004, mapped to the runtime flag that replaced them. Caught
# before generic validation so the error explains *where the engine went*, not
# just that an unknown key is present.
_REMOVED_KEYS: dict[str, str] = {
    "skill.backend": "--model",
    "skill.model": "--model",
    "judge": "--judge-model",
}


def _reject_removed_keys(raw: dict) -> None:
    offenders: list[str] = []
    skill = raw.get("skill")
    if isinstance(skill, dict):
        offenders += [f"skill.{k}" for k in ("backend", "model") if k in skill]
    if "judge" in raw:
        offenders.append("judge")
    if not offenders:
        return
    lines = [
        f"  - {key} (engine now comes from {_REMOVED_KEYS[key]})" for key in offenders
    ]
    raise ValueError(
        "backend/model are no longer spec fields — the engine is chosen at "
        "runtime (default: "
        f"{DEFAULT_BACKEND}). Delete these key(s) from the spec:\n"
        + "\n".join(lines)
        + "\nPass the engine when you run: "
        "caliper run <spec> --model codex:gpt-5-codex --judge-model claude-code"
    )


def load_spec(path: Path) -> EvalSpec:
    import yaml

    raw = yaml.safe_load(path.read_text())
    if isinstance(raw, dict):
        _reject_removed_keys(raw)
    for i, task in enumerate(raw.get("tasks", []), 1):
        task["id"] = f"task-{i:03d}"
    return EvalSpec.model_validate(raw)


def spec_name(path: Path) -> str:
    name = path.stem
    if name.endswith(".eval"):
        name = name[: -len(".eval")]
    return name
