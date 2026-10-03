"""Docs that must track the CLI and the spec format (see AGENTS.md → Updating docs)."""

from __future__ import annotations

import re
import typing
from pathlib import Path

from pydantic import BaseModel
from typer.main import get_command

from caliper.main import app
from caliper.schema.spec import EvalSpec

ROOT = Path(__file__).resolve().parent.parent

# Assigned by the loader from the task's position, never written in a spec.
_LOADER_KEYS = {"id"}


def _run_flags() -> dict[str, set[str]]:
    """Each `caliper run` option's primary flag, mapped to every spelling of it."""
    run = get_command(app).commands["run"]
    flags = {
        param.opts[0]: set(param.opts) | set(param.secondary_opts)
        for param in run.params
        if param.param_type_name == "option" and param.opts[0].startswith("--")
    }
    # An empty result would make every check below pass vacuously.
    assert flags, "found no `caliper run` options; did typer change its types?"
    return flags


def _mentions_flag(text: str, flag: str) -> bool:
    return re.search(re.escape(flag) + r"(?![\w-])", text) is not None


def _readme_run_flags_table() -> str:
    readme = (ROOT / "README.md").read_text()
    section = readme.split("### `caliper run` flags", 1)[1]
    return section.split("\n#", 1)[0]


def test_readme_run_flags_table_lists_every_run_flag() -> None:
    table = _readme_run_flags_table()
    missing = [
        flag
        for flag, spellings in _run_flags().items()
        if not any(_mentions_flag(table, s) for s in spellings)
    ]
    assert missing == [], f"add to README.md's `caliper run` flags table: {missing}"


def test_readme_run_flags_table_has_no_stale_flags() -> None:
    known = set().union(*_run_flags().values())
    documented = set(re.findall(r"--[a-z][\w-]*", _readme_run_flags_table()))
    assert documented - known == set(), "flags `caliper run` no longer has"


def test_evaluate_skill_reference_mentions_every_run_flag() -> None:
    reference = (ROOT / "skills/evaluate-skill/REFERENCE.md").read_text()
    missing = [
        flag
        for flag, spellings in _run_flags().items()
        if not any(_mentions_flag(reference, s) for s in spellings)
    ]
    assert missing == [], f"add to skills/evaluate-skill/REFERENCE.md: {missing}"


def _spec_models(model: type[BaseModel], seen: list[type[BaseModel]]) -> None:
    if model in seen:
        return
    seen.append(model)
    for field in model.model_fields.values():
        pending = [field.annotation]
        while pending:
            annotation = pending.pop()
            if isinstance(annotation, type) and issubclass(annotation, BaseModel):
                _spec_models(annotation, seen)
            pending.extend(typing.get_args(annotation))


def test_spec_reference_documents_every_spec_key() -> None:
    reference = (ROOT / "docs/spec-reference.md").read_text()
    models: list[type[BaseModel]] = []
    _spec_models(EvalSpec, models)
    missing = [
        f"{model.__name__}.{key}"
        for model in models
        for name, field in model.model_fields.items()
        if (key := field.alias or name) not in _LOADER_KEYS
        and not re.search(rf"(?<![\w-]){re.escape(key)}:|`{re.escape(key)}`", reference)
    ]
    assert missing == [], f"document in docs/spec-reference.md: {missing}"
