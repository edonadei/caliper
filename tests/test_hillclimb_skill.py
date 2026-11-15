"""hillclimb-skill's helper keeps held-out tasks out of the climber's sight.

`skills/hillclimb-skill/climb.py` is stdlib-only and ships with the skill, so it
is loaded by path here and fed the JSON Caliper really writes.
"""

from __future__ import annotations

import importlib.util
import io
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from caliper.compare import diff_runs
from caliper.reporter import comparison_to_json
from caliper.schema.results import (
    AggregateScore,
    AttemptRecord,
    Outcome,
    RunMeta,
    RunResults,
    SkillSnapshot,
    TaskResult,
)

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "climb", ROOT / "skills/hillclimb-skill/climb.py"
)
climb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(climb)

NAMES = [f"task {i}" for i in range(9)]


def _task(name: str, outcomes: list[Outcome]) -> TaskResult:
    return TaskResult(
        task_id=name,
        task_name=name,
        prompt=f"prompt of {name}",
        expect=f"expect of {name}",
        attempts=[
            AttemptRecord(
                attempt=i + 1,
                output="",
                duration_seconds=1.0,
                outcome=o,
                autorater_reasoning=f"reasoning for {name}",
            )
            for i, o in enumerate(outcomes)
        ],
    )


def _run(outcomes: list[Outcome], names: list[str] = NAMES) -> RunResults:
    tasks = [_task(name, outcomes) for name in names]
    return RunResults(
        run=RunMeta(
            spec="demo",
            timestamp=datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc),
            k=len(outcomes),
            backend="claude-code",
        ),
        skill_snapshots=[SkillSnapshot(path="/fake/SKILL.md")],
        task_results=tasks,
        aggregate=AggregateScore.from_task_results(tasks, k=len(outcomes)),
    )


def _save(path: Path, run: RunResults) -> Path:
    path.write_text(run.model_dump_json(), encoding="utf-8")
    return path


@pytest.fixture
def climbing(tmp_path):
    """A failing run on disk, already split."""
    run = _save(tmp_path / "run.json", _run([Outcome.TASK_FAIL, Outcome.PASS]))
    state = tmp_path / "hillclimb" / "state.json"
    climb.main(["split", str(run), str(state), "--surface", "body", "--seed", "1"])
    return run, state


def test_split_holds_out_a_third_and_is_fixed_for_the_climb(climbing):
    run, state = climbing
    split = json.loads(state.read_text())

    assert len(split["held_out"]) == 3
    assert sorted(split["train"] + split["held_out"]) == NAMES
    with pytest.raises(SystemExit, match="fixed for the whole climb"):
        climb.main(["split", str(run), str(state), "--surface", "body"])


def test_split_refuses_an_eval_too_small_to_hold_tasks_out(tmp_path):
    run = _save(tmp_path / "run.json", _run([Outcome.PASS], NAMES[:5]))

    with pytest.raises(SystemExit, match="5 tasks measure the body"):
        climb.main(
            ["split", str(run), str(tmp_path / "state.json"), "--surface", "body"]
        )


def test_failures_shows_train_tasks_only(climbing, capsys):
    run, state = climbing
    split = json.loads(state.read_text())
    capsys.readouterr()

    climb.main(["failures", str(run), str(state)])
    out = capsys.readouterr().out

    assert all(f"## {name} " in out for name in split["train"])
    assert not any(name in out for name in split["held_out"])
    with pytest.raises(SystemExit, match="not a train task"):
        climb.main(["failures", str(run), str(state), "--task", split["held_out"][0]])


def test_scores_reads_each_split_from_compare_json(climbing, capsys, monkeypatch):
    _, state = climbing
    before = _run([Outcome.TASK_FAIL, Outcome.PASS])
    after = _run([Outcome.PASS, Outcome.PASS])
    monkeypatch.setattr(
        "sys.stdin", io.StringIO(comparison_to_json(diff_runs(before, after)))
    )
    capsys.readouterr()

    climb.main(["scores", str(state)])
    out = capsys.readouterr().out

    assert "train     50% -> 100%  (+50 pp)  6/6 tasks measured" in out
    assert "held-out  50% -> 100%  (+50 pp)  3/3 tasks measured" in out
