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


def _project(tmp_path: Path, *modules: str) -> Path:
    """A project whose floor covers ``caliper/*.py``, holding ``modules``."""
    (tmp_path / "pyproject.toml").write_text(
        '[tool.coverage.report]\ninclude = ["caliper/*.py"]\nfail_under = 95\n',
        encoding="utf-8",
    )
    (tmp_path / "caliper").mkdir()
    for module in modules:
        (tmp_path / module).write_text("", encoding="utf-8")
    return tmp_path


def _main(root: Path, files: dict) -> int:
    report = root / "coverage.json"
    report.write_text(json.dumps({"files": files}), encoding="utf-8")
    return coverage_floor.main([str(report)], root=root)


def test_the_script_reads_the_floor_from_pyproject_and_fails_below_it(tmp_path):
    root = _project(tmp_path, "caliper/small.py")
    assert _main(root, {"caliper/small.py": _file(90.0, missing=[7])}) == 1
    assert _main(root, {"caliper/small.py": _file(95.0, 95.0)}) == 0


def test_a_module_the_report_never_measured_fails(tmp_path, capsys):
    # `pytest --cov=caliper.compare` writes a report of compare.py alone.
    root = _project(tmp_path, "caliper/compare.py", "caliper/retry.py")
    assert _main(root, {"caliper/compare.py": _file()}) == 1
    assert "caliper/retry.py: not in the coverage report" in capsys.readouterr().err


def test_a_report_without_branch_data_fails_saying_so(tmp_path, capsys):
    root = _project(tmp_path, "caliper/retry.py")
    lines_only = _file()
    del lines_only["summary"]["percent_branches_covered"]
    assert _main(root, {"caliper/retry.py": lines_only}) == 1
    assert "no branch data" in capsys.readouterr().err
