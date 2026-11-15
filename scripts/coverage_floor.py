"""Fail when any module in ``[tool.coverage.report] include`` is below the floor.

``coverage report --fail-under`` checks the total, so one big module at 100%
hides a small one at 80%. Run after ``pytest --cov`` and ``coverage json``:
``python scripts/coverage_floor.py coverage.json``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

ROOT = Path(__file__).resolve().parent.parent


def below_floor(report: dict, floor: float) -> list[str]:
    """One line per file under ``floor``, naming what the tests never reach."""
    lines = []
    for path, data in sorted(report["files"].items()):
        pct = data["summary"]["percent_covered"]
        if pct < floor:
            branches = [f"{a}->{b}" for a, b in data.get("missing_branches", [])]
            lines.append(
                f"{path}: {pct:.1f}% < {floor:g}%; "
                f"missing lines {data['missing_lines']}, branches {branches}"
            )
    return lines


def main(argv: list[str], root: Path = ROOT) -> int:
    with (root / "pyproject.toml").open("rb") as f:
        floor = tomllib.load(f)["tool"]["coverage"]["report"]["fail_under"]
    report = json.loads(Path(argv[0]).read_text(encoding="utf-8"))
    failures = below_floor(report, floor)
    for line in failures:
        print(line, file=sys.stderr)
    if failures:
        print(
            "Each pure domain module needs its own branch-coverage floor: add tests "
            "for the lines and branches above.",
            file=sys.stderr,
        )
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
