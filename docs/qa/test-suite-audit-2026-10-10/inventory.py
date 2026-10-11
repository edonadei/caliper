from __future__ import annotations

import ast
import json
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ARTIFACTS = Path(__file__).resolve().parent
counts = Counter()
case_results = Counter()
xml = ARTIFACTS / "baseline.xml"
for case in ET.parse(xml).iter("testcase"):
    counts[case.get("classname").removeprefix("tests.")] += 1
    result = (
        "failure"
        if case.find("failure") is not None
        else "skipped"
        if case.find("skipped") is not None
        else "passed"
    )
    case_results[result] += 1
rows = []
for path in sorted((ROOT / "tests").glob("test_*.py")):
    tree = ast.parse(path.read_text())
    tests = []
    for node in ast.walk(tree):
        if isinstance(
            node, (ast.FunctionDef, ast.AsyncFunctionDef)
        ) and node.name.startswith("test_"):
            direct = sum(isinstance(item, ast.Assert) for item in ast.walk(node))
            raises = sum(
                isinstance(item, ast.Call)
                and isinstance(item.func, ast.Attribute)
                and item.func.attr == "raises"
                for item in ast.walk(node)
            )
            tests.append(
                {
                    "name": node.name,
                    "line": node.lineno,
                    "assertions": direct,
                    "raises": raises,
                }
            )
    rows.append(
        {
            "module": path.name,
            "lines": len(path.read_text().splitlines()),
            "functions": len(tests),
            "cases": counts[path.stem],
            "tests": tests,
        }
    )
report = {
    "sha": "e8a9302fa355653f95ff7c6d46b4d94ece9cae3c",
    "modules": len(rows),
    "functions": sum(row["functions"] for row in rows),
    "cases": sum(counts.values()),
    "results": dict(case_results),
    "inventory": rows,
}
(ARTIFACTS / "inventory.json").write_text(json.dumps(report, indent=2) + "\n")
print(
    json.dumps(
        {key: value for key, value in report.items() if key != "inventory"}, indent=2
    )
)
print(
    "Tests without a direct assert or pytest.raises. These are prompts for inspection, not deletion rules."
)
for row in rows:
    for test in row["tests"]:
        if not test["assertions"] and not test["raises"]:
            print(f"{row['module']}:{test['line']} {test['name']}")
