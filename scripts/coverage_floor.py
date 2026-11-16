"""Fail when any module in ``[tool.coverage.report] include`` is below the floor.

Lines and branches are each held to it, per module, and an unmeasured module
fails: ``--fail-under`` checks one total, where a big module hides a small one.
Run after ``pytest --cov``: ``python scripts/coverage_floor.py coverage.json``.
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
        summary = data["summary"]
        covered = summary["percent_statements_covered"]
        branched = summary.get("percent_branches_covered")
        if branched is None:
            lines.append(f"{path}: no branch data; measure with `branch = true`")
        elif covered < floor or branched < floor:
            branches = [f"{a}->{b}" for a, b in data["missing_branches"]]
            lines.append(
                f"{path}: lines {covered:.1f}%, branches {branched:.1f}% "
                f"(floor {floor:g}%); "
                f"missing lines {data['missing_lines']}, branches {branches}"
            )
    return lines


def unmeasured(report: dict, include: list[str], root: Path) -> list[str]:
    """One line per module the floor covers that the report has no entry for."""
    measured = {Path(path).as_posix() for path in report["files"]}
    modules = {p.relative_to(root).as_posix() for g in include for p in root.glob(g)}
    return [
        f"{module}: not in the coverage report; run the whole suite with `--cov`"
        for module in sorted(modules - measured)
    ]


def main(argv: list[str], root: Path = ROOT) -> int:
    with (root / "pyproject.toml").open("rb") as f:
        config = tomllib.load(f)["tool"]["coverage"]["report"]
    report = json.loads(Path(argv[0]).read_text(encoding="utf-8"))
    failures = unmeasured(report, config["include"], root) + below_floor(
        report, config["fail_under"]
    )
    for line in failures:
        print(line, file=sys.stderr)
    if failures:
        print(
            "Each pure domain module is held to the floor on its own: measure every "
            "one, and add tests for the lines and branches above.",
            file=sys.stderr,
        )
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
