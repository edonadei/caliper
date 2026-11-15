"""The coverage floor holds each pure domain module to it on its own.

`scripts/coverage_floor.py` is what CI runs after the suite; it reads the JSON
`coverage json` writes, so the tests feed it that shape.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "coverage_floor", ROOT / "scripts/coverage_floor.py"
)
coverage_floor = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(coverage_floor)


def _report(files: dict[str, tuple[float, list[int], list[list[int]]]]) -> dict:
    return {
        "files": {
            path: {
                "summary": {"percent_covered": pct},
                "missing_lines": missing,
                "missing_branches": branches,
            }
            for path, (pct, missing, branches) in files.items()
        }
    }


def test_every_module_at_the_floor_passes():
    report = _report({"caliper/a.py": (95.0, [], []), "caliper/b.py": (100.0, [], [])})
    assert coverage_floor.below_floor(report, 95) == []


def test_a_strong_module_does_not_carry_a_weak_one():
    report = _report(
        {"caliper/big.py": (100.0, [], []), "caliper/small.py": (80.0, [7], [[3, 5]])}
    )
    assert coverage_floor.below_floor(report, 95) == [
        "caliper/small.py: 80.0% < 95%; missing lines [7], branches ['3->5']"
    ]


def test_the_script_reads_the_floor_from_pyproject_and_fails_below_it(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        "[tool.coverage.report]\nfail_under = 95\n", encoding="utf-8"
    )
    report = tmp_path / "coverage.json"
    report.write_text(
        json.dumps(_report({"caliper/small.py": (90.0, [7], [])})), encoding="utf-8"
    )
    assert coverage_floor.main([str(report)], root=tmp_path) == 1
    report.write_text(
        json.dumps(_report({"caliper/small.py": (95.0, [], [])})), encoding="utf-8"
    )
    assert coverage_floor.main([str(report)], root=tmp_path) == 0
