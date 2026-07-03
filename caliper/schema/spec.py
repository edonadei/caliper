from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator


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


class EvalSpec(BaseModel):
    skill: SkillConfig
    sandbox: SandboxConfig = Field(default_factory=SandboxConfig)
    tasks: list[TaskSpec]

    model_config = ConfigDict(extra="forbid")


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
