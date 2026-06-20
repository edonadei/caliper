from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


BackendName = Literal["claude-code", "codex", "claude-api", "openai-api"]

_VALID_BACKENDS: frozenset[str] = frozenset({"claude-code", "codex", "claude-api", "openai-api"})


def normalize_backend(value: str) -> str:
    aliases = {
        "claude": "claude-code",
        "anthropic": "claude-api",
        "openai": "openai-api",
    }
    return aliases.get(value, value)


def parse_target(value: str) -> tuple[str | None, str | None]:
    """Parse a --model / --judge-model value into (backend, model).

    Accepts three forms:
      "claude-api:claude-sonnet-4-6"  -> ("claude-api", "claude-sonnet-4-6")
      "codex"                         -> ("codex", None)   # known backend name
      "claude-sonnet-4-6"             -> (None, "claude-sonnet-4-6")
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
            raise ValueError(f"Task '{self.name}' must have at least one of: expect, assert")
        return self


class SkillConfig(BaseModel):
    path: str | None = None
    backend: BackendName = "claude-code"
    model: str | None = None

    @field_validator("backend", mode="before")
    @classmethod
    def normalize_backend_name(cls, value: str) -> str:
        return normalize_backend(value)


class JudgeConfig(BaseModel):
    backend: BackendName = "claude-code"
    model: str | None = None

    @field_validator("backend", mode="before")
    @classmethod
    def normalize_backend_name(cls, value: str) -> str:
        return normalize_backend(value)


class SandboxConfig(BaseModel):
    forbidden_files: list[str] = []
    extra_path: list[str] = []


class EvalSpec(BaseModel):
    skill: SkillConfig
    judge: JudgeConfig = Field(default_factory=JudgeConfig)
    sandbox: SandboxConfig = Field(default_factory=SandboxConfig)
    tasks: list[TaskSpec]

    model_config = ConfigDict(extra="forbid")


def load_spec(path: Path) -> EvalSpec:
    import yaml

    raw = yaml.safe_load(path.read_text())
    for i, task in enumerate(raw.get("tasks", []), 1):
        task["id"] = f"task-{i:03d}"
    return EvalSpec.model_validate(raw)


def spec_name(path: Path) -> str:
    name = path.stem
    if name.endswith(".eval"):
        name = name[: -len(".eval")]
    return name
