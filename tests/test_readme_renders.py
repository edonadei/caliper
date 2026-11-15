"""README SVG sample fixtures and committed SVG renders stay in sync."""

from __future__ import annotations

import ast
import importlib.util
import re
from pathlib import Path
from types import ModuleType

import pytest
from pydantic import BaseModel

import caliper.reporter as reporter
import caliper.schema.results as results_schema

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "docs" / "assets"
_RICH_HASH_RE = re.compile(r"\bterminal-\d+-")


def _load_script(rel_path: str) -> ModuleType:
    path = ROOT / rel_path
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _normalize_svg(svg: str) -> str:
    """Normalize Rich's adler32 class prefix so minor Rich upgrades stay stable."""
    return _RICH_HASH_RE.sub("terminal-HASH-", svg)


def test_sample_examples_derive_expected_comparison_metrics() -> None:
    samples = _load_script("docs/render_readme_samples.py")

    ablation = samples._ablation_example()
    assert ablation.a_matched_avg == pytest.approx(0.8)
    assert ablation.b_matched_avg == pytest.approx(1.0)
    assert ablation.aggregate_delta == pytest.approx(0.2)
    assert ablation.has_regression is False

    comparison = samples._compare_example()
    assert comparison.a_matched_avg == pytest.approx(1.0)
    assert comparison.b_matched_avg == pytest.approx(0.6)
    assert comparison.aggregate_delta == pytest.approx(-0.4)
    assert comparison.has_regression is True


def test_readme_sample_fixtures_pass_no_computed_fields() -> None:
    computed_by_model = {
        name: set(obj.model_computed_fields)
        for name, obj in vars(results_schema).items()
        if isinstance(obj, type)
        and issubclass(obj, BaseModel)
        and obj.model_computed_fields
    }
    source_path = ROOT / "docs" / "render_readme_samples.py"
    tree = ast.parse(source_path.read_text(encoding="utf-8"))

    redundant: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func_name = (
            node.func.id
            if isinstance(node.func, ast.Name)
            else node.func.attr
            if isinstance(node.func, ast.Attribute)
            else None
        )
        if func_name not in computed_by_model:
            continue
        for kw in node.keywords:
            if kw.arg in computed_by_model[func_name]:
                redundant.append(f"line {node.lineno}: {func_name}({kw.arg}=...)")

    assert redundant == [], (
        f"docs/render_readme_samples.py passes ignored @computed_field kwargs: "
        f"{redundant}"
    )


@pytest.mark.parametrize(
    ("script_rel_path", "expected_svgs"),
    [
        (
            "docs/render_readme_samples.py",
            {"compare-ablation.svg", "compare-runs.svg", "run-output.svg"},
        ),
        (
            "docs/render_readme_hero.py",
            {"hero-dark.svg", "hero-light.svg"},
        ),
    ],
)
def test_readme_svgs_are_up_to_date(
    monkeypatch: pytest.MonkeyPatch,
    script_rel_path: str,
    expected_svgs: set[str],
) -> None:
    monkeypatch.setenv("NO_COLOR", "1")
    # Simulate a non-UTF-8 host console (e.g. Windows cp1252 or LC_ALL=C) to
    # verify SVG exports still render Unicode glyphs and rounded borders.
    prev_unicode = reporter._UNICODE
    reporter._configure_glyphs(False)
    try:
        rendered_by_name = _load_script(script_rel_path).render_all()
    finally:
        reporter._configure_glyphs(prev_unicode)

    assert set(rendered_by_name) == expected_svgs

    for name, rendered in sorted(rendered_by_name.items()):
        committed = (ASSETS / name).read_text(encoding="utf-8")
        assert _normalize_svg(rendered) == _normalize_svg(committed), (
            f"docs/assets/{name} is out of date; regenerate with: "
            f"python {script_rel_path}"
        )
