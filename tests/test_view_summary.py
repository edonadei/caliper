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


def _summary(states: dict[str, str]) -> tuple[str, str]:
    """The summary, split into its shown part and its folded part."""
    md = view_summary.summary(states, BEFORE, AFTER)
    shown, _, folded = md.partition("<details>")
    return shown, folded


def test_a_changed_view_shows_before_and_after():
    shown, _ = _summary({"compare-runs.svg": "unchanged", "run.svg": "changed"})
    assert "### `run.svg`" in shown
    assert f"![before]({BEFORE}/docs/assets/run.svg)" in shown
    assert f"![after]({AFTER}/docs/assets/run.svg)" in shown
    assert "compare-runs.svg" not in shown


def test_an_unchanged_view_stays_folded_with_its_current_render():
    _, folded = _summary({"compare-runs.svg": "unchanged", "run.svg": "changed"})
    assert "<summary>Unchanged: 1 view</summary>" in folded
    assert f"![compare-runs.svg]({AFTER}/docs/assets/compare-runs.svg)" in folded
    assert BEFORE not in folded


def test_a_new_view_has_no_before_and_a_removed_one_no_after():
    shown, _ = _summary({"new.svg": "new", "old.svg": "removed"})
    assert f"![new]({AFTER}/docs/assets/new.svg)" in shown
    assert f"{BEFORE}/docs/assets/new.svg" not in shown
    assert f"![removed]({BEFORE}/docs/assets/old.svg)" in shown
    assert f"{AFTER}/docs/assets/old.svg" not in shown


def test_a_pr_that_changes_no_view_says_which_views_it_checked():
    shown, folded = _summary({"run.svg": "unchanged"})
    assert "This PR changes none of the sampled views" in shown
    assert "<summary>Unchanged: 1 view</summary>" in folded


def test_a_rich_hash_change_alone_is_not_a_change():
    before = '<svg class="terminal-123-matrix">x</svg>'
    after = '<svg class="terminal-456-matrix">x</svg>'
    assert view_summary.state(before, after) == "unchanged"
    assert view_summary.state(before, after.replace("x", "y")) == "changed"
    assert view_summary.state(None, after) == "new"
    assert view_summary.state(before, None) == "removed"


def test_the_views_are_the_ones_the_readme_scripts_render():
    assert view_summary.views() == [
        "compare-ablation.svg",
        "compare-runs.svg",
        "hero-dark.svg",
        "hero-light.svg",
        "run-output.svg",
    ]
