"""`caliper list <spec> --format json`: the runs listing for an agent to read.

The table folds and marks its cells for a person; an agent scraping it to find
the control arm misreads a wrapped run id. These pin the JSON it reads instead.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from typer.testing import CliRunner

from caliper.main import app
from caliper.schema.results import (
    ERA_INSTALL_AND_DISCOVER,
    AggregateScore,
    AttemptRecord,
    Outcome,
    RunMeta,
    RunResults,
    SkillSnapshot,
    TaskResult,
)

runner = CliRunner()


def _save(
    root: Path,
    stem: str,
    *,
    ablated: tuple[str, ...] = (),
    interrupted: bool = False,
    scored_tasks: int = 1,
) -> Path:
    results = RunResults(
        run=RunMeta(
            spec="demo",
            timestamp=datetime(2026, 8, 4, tzinfo=timezone.utc),
            k=3,
            backend="claude-code",
            era=ERA_INSTALL_AND_DISCOVER,
            ablated=list(ablated),
            interrupted=interrupted,
        ),
        # Installed unless ablated, so `compare` can tell the arms apart.
        skill_snapshots=[
            SkillSnapshot(name=name, path=f"/x/{name}/SKILL.md")
            for name in {"my-skill"} - set(ablated)
        ],
        task_results=[
            TaskResult(
                task_id=f"task-00{n}",
                task_name=f"task {n}",
                attempts=[
                    AttemptRecord(
                        attempt=1, output="", duration_seconds=1.0, outcome=Outcome.PASS
                    )
                ],
            )
            for n in (1, 2)
        ],
        aggregate=AggregateScore(
            avg_score=0.75, scored_tasks=scored_tasks, per_task=[]
        ),
    )
    out = root / ".caliper" / "results" / "demo"
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{stem}.json"
    path.write_text(results.model_dump_json())
    return path


def _listed(*argv: str) -> list[dict]:
    result = runner.invoke(app, ["list", "demo", "--format", "json", *argv])
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


def test_each_run_is_an_object_naming_its_path(monkeypatch, tmp_path) -> None:
    control = _save(tmp_path, "2026-08-04T10-00-00Z", ablated=("my-skill",))
    _save(tmp_path, "2026-08-04T10-30-00Z")
    monkeypatch.chdir(tmp_path)

    first, second = _listed()

    assert first == {
        "run": "2026-08-04T10-00-00Z",
        "path": str(control.resolve()),
        "k": 3,
        "tasks": 2,
        "score": 0.75,
        "ablated": ["my-skill"],
        "interrupted": False,
    }
    assert (second["run"], second["ablated"]) == ("2026-08-04T10-30-00Z", [])


def test_the_listed_path_is_what_compare_takes(monkeypatch, tmp_path) -> None:
    # The point of the listing: find the control arm, then diff against it.
    _save(tmp_path, "2026-08-04T10-00-00Z", ablated=("my-skill",))
    _save(tmp_path, "2026-08-04T10-30-00Z")
    monkeypatch.chdir(tmp_path)
    control = next(r for r in _listed() if r["ablated"])

    result = runner.invoke(app, ["compare", control["path"], "demo"])

    assert result.exit_code == 0, result.output
    assert "without my-skill" in result.output


def test_an_interrupted_run_says_so(monkeypatch, tmp_path) -> None:
    _save(tmp_path, "2026-08-04T10-00-00Z", interrupted=True)
    monkeypatch.chdir(tmp_path)

    (run,) = _listed()

    assert run["interrupted"] is True


def test_an_unmeasured_run_has_no_score(monkeypatch, tmp_path) -> None:
    # The table prints a rule here, not 0.0%: nothing was asked, so nothing failed.
    _save(tmp_path, "2026-08-04T10-00-00Z", scored_tasks=0)
    monkeypatch.chdir(tmp_path)

    (run,) = _listed()

    assert run["score"] is None
    assert "error" not in run


def test_an_unreadable_run_keeps_its_row_and_says_why(monkeypatch, tmp_path) -> None:
    # Only the JSON is on stdout: the table's "Skipping unreadable run" warning
    # would make it unparseable, so the reason rides in the row instead.
    _save(tmp_path, "2026-08-04T10-00-00Z")
    bad = _save(tmp_path, "2026-08-04T10-30-00Z")
    bad.write_text("not json at all")
    monkeypatch.chdir(tmp_path)

    good, broken = _listed()

    assert good["score"] == 0.75
    assert broken["run"] == "2026-08-04T10-30-00Z"
    assert broken["path"] == str(bad.resolve())
    assert broken["score"] is None
    assert "Invalid JSON" in broken["error"]
    assert {broken[key] for key in ("k", "tasks", "ablated", "interrupted")} == {None}


def test_a_spec_with_its_runs_cleared_lists_none(monkeypatch, tmp_path) -> None:
    (tmp_path / ".caliper" / "results" / "demo").mkdir(parents=True)
    monkeypatch.chdir(tmp_path)

    assert _listed() == []


def test_json_needs_a_spec(monkeypatch, tmp_path) -> None:
    _save(tmp_path, "2026-08-04T10-00-00Z")
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["list", "--format", "json"])

    assert result.exit_code == 1
    assert "caliper list <spec> --format json" in result.output


def test_an_unknown_spec_is_still_refused(monkeypatch, tmp_path) -> None:
    _save(tmp_path, "2026-08-04T10-00-00Z")
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["list", "nope", "--format", "json"])

    assert result.exit_code == 1
    assert "nope" in result.output
