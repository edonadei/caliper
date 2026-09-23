"""The live grounding demo stays a valid spec with a working lookup tool."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from caliper.schema.spec import load_spec

EXAMPLE = Path(__file__).parent.parent / "examples" / "tool-grounding"


def test_the_demo_spec_validates_and_grades_with_classify():
    spec = load_spec(EXAMPLE / "grounding.eval.yaml")
    assert set(spec.mcp) == {"deployments"}
    for task in spec.tasks:
        (check,) = task.classify
        assert (check.evidence, check.require, check.abstain) == (
            "tool_trace",
            "grounded",
            "unclear",
        )
        assert check.min_probability == 0.8
        assert not task.expect and not task.assert_script


def test_the_lookup_tool_answers_over_stdio():
    request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "lookup_deployment_window",
            "arguments": {"service": "billing-worker"},
        },
    }
    proc = subprocess.run(
        [sys.executable, str(EXAMPLE / "servers" / "deployments.py")],
        input=json.dumps(request) + "\n",
        capture_output=True,
        text=True,
        timeout=10,
    )
    text = json.loads(proc.stdout)["result"]["content"][0]["text"]
    assert json.loads(text)["status"] == "frozen"
