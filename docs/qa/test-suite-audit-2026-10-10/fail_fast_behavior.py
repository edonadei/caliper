from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
fixtures = importlib.import_module("conftest")
runner = importlib.import_module("caliper.runner")
spec_types = importlib.import_module("caliper.schema.spec")
ScriptedHarness = fixtures.ScriptedHarness
ScriptedJudge = fixtures.ScriptedJudge
agent_result = fixtures.agent_result
in_turn = fixtures.in_turn
EvalSpec = spec_types.EvalSpec
TaskSpec = spec_types.TaskSpec


def observe():
    with TemporaryDirectory() as directory:
        path = Path(directory) / "probe.eval.yaml"
        path.write_text("tasks: []\n")
        timed_out = agent_result(
            transcript=[], final_output="", exit_code=124, timed_out=True
        )
        harness = ScriptedHarness(
            in_turn(timed_out, agent_result(), timed_out, agent_result())
        )
        results = runner.run(
            spec=EvalSpec(tasks=[TaskSpec(name="silence", prompt="p", activates=[])]),
            spec_path=path,
            harness=harness,
            judge=ScriptedJudge(),
            k=4,
            workers=1,
            timeout=30,
            fail_fast_unusable=2,
        )
        return [a.outcome.value for a in results.task_results[0].attempts]


original = observe()
source = Path(runner.__file__).read_text()
needle = "elif not record.outcome.is_execution_noise:"
assert source.count(needle) == 1
replacement = "elif not record.outcome.is_execution_noise and record.outcome is not Outcome.NOT_CHECKED:"
exec(
    compile(source.replace(needle, replacement), runner.__file__, "exec"),
    runner.__dict__,
)
mutated = observe()
assert original == ["timeout", "not_checked", "timeout", "not_checked"]
assert mutated == ["timeout", "not_checked", "timeout"]
print(json.dumps({"original": original, "mutated": mutated}, indent=2))
