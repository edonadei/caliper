"""A PR's job summary shows the terminal views it changes, before and after.

`scripts/view_summary.py` runs in `.github/workflows/views.yml`; the freshness
test in tests/test_readme_renders.py makes the committed SVGs the real renders.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "view_summary", ROOT / "scripts/view_summary.py"
)
view_summary = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(view_summary)

BEFORE = "https://raw.githubusercontent.com/o/caliper/aaa"
AFTER = "https://raw.githubusercontent.com/fork/caliper/bbb"


def test_a_changed_view_shows_before_and_after():
    md = view_summary.summary(
        ["compare-runs.svg", "run-output.svg"], {"run-output.svg"}, BEFORE, AFTER
    )
    changed = md.split("<details>")[0]
    assert "### `run-output.svg`" in changed
    assert f"![before]({BEFORE}/docs/assets/run-output.svg)" in changed
    assert f"![after]({AFTER}/docs/assets/run-output.svg)" in changed
    assert "compare-runs.svg" not in changed


def test_an_unchanged_view_stays_folded_with_its_current_render():
    md = view_summary.summary(
        ["compare-runs.svg", "run-output.svg"], {"run-output.svg"}, BEFORE, AFTER
    )
    folded = md.split("<details>")[1]
    assert "<summary>Unchanged: 1 view</summary>" in folded
    assert f"![compare-runs.svg]({AFTER}/docs/assets/compare-runs.svg)" in folded
    assert BEFORE not in folded


def test_a_pr_that_changes_no_view_says_so():
    md = view_summary.summary(["run-output.svg"], set(), BEFORE, AFTER)
    assert "This PR changes no terminal view." in md
    assert "<summary>Unchanged: 1 view</summary>" in md


def test_the_views_are_the_ones_the_readme_sample_script_renders():
    assert view_summary.views() == [
        "compare-ablation.svg",
        "compare-runs.svg",
        "run-output.svg",
    ]
