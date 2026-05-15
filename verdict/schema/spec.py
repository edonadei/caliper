from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


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
    backend: Literal["claude", "codex"] = "claude"
    model: str | None = None


class JudgeConfig(BaseModel):
    backend: Literal["claude", "codex"] = "claude"
    model: str | None = None


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
