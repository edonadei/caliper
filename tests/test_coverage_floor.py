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


def _file(
    lines: float = 100.0,
    branches: float = 100.0,
    missing: list[int] | None = None,
    missing_branches: list[list[int]] | None = None,
) -> dict:
    return {
        "summary": {
            "percent_statements_covered": lines,
            "percent_branches_covered": branches,
        },
        "missing_lines": missing or [],
        "missing_branches": missing_branches or [],
    }


def test_every_module_at_the_floor_passes():
    report = {"files": {"caliper/a.py": _file(95.0, 95.0), "caliper/b.py": _file()}}
    assert coverage_floor.below_floor(report, 95) == []


def test_a_strong_module_does_not_carry_a_weak_one():
    report = {
        "files": {
            "caliper/big.py": _file(),
            "caliper/small.py": _file(80.0, 50.0, [7], [[3, 5]]),
        }
    }
    assert coverage_floor.below_floor(report, 95) == [
        "caliper/small.py: lines 80.0%, branches 50.0% (floor 95%); "
        "missing lines [7], branches ['3->5']"
    ]


def test_full_line_coverage_does_not_carry_missed_branches():
    # A combined percentage would read 98.6% here and pass.
    report = {"files": {"caliper/retry.py": _file(100.0, 92.9, [], [[179, 198]])}}
    assert coverage_floor.below_floor(report, 95) == [
        "caliper/retry.py: lines 100.0%, branches 92.9% (floor 95%); "
        "missing lines [], branches ['179->198']"
    ]


def test_the_script_reads_the_floor_from_pyproject_and_fails_below_it(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        "[tool.coverage.report]\nfail_under = 95\n", encoding="utf-8"
    )
    report = tmp_path / "coverage.json"
    report.write_text(
        json.dumps({"files": {"caliper/small.py": _file(90.0, missing=[7])}}),
        encoding="utf-8",
    )
    assert coverage_floor.main([str(report)], root=tmp_path) == 1
    report.write_text(
        json.dumps({"files": {"caliper/small.py": _file(95.0, 95.0)}}),
        encoding="utf-8",
    )
    assert coverage_floor.main([str(report)], root=tmp_path) == 0
