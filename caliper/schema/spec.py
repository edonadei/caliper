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


def normalize_backend(value: str) -> str:
    aliases = {
        "claude": "claude-code",
    }
    return aliases.get(value, value)


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


# A declared MCP server the agent-under-test may use. This slice supports only
# *local stdio* servers: a `command` (with `args`) the harness spawns, plus an
# `env` map whose values may reference host env vars as ``${VAR}`` so secrets
# never live in the committed spec. Transport is inferred from shape (the
# presence of `command` means stdio); a `type:` discriminator and remote/HTTP
# servers are a later slice. See docs/adr/0008-mcp-servers-are-a-spec-field.md
# and docs/adr/0009-mcp-secrets-interpolated-at-the-harness-boundary.md.
class McpServer(BaseModel):
    command: str
    args: list[str] = []
    env: dict[str, str] = {}

    model_config = ConfigDict(extra="forbid")

    @field_validator("command")
    @classmethod
    def command_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("command must be a non-empty string")
        return value


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
