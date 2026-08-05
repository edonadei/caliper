"""``caliper compare``'s run *addressing* — which saved file each argument means.

A bare spec name resolves to that spec's **latest** run, so naming one twice
resolves to a single file. Since an ablated control arm and the full run are two
runs of the same spec, that is now an easy mistake to make — and a run diffed
against itself renders a clean table of zeros with no guard tripped.
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


def _write_run(
    root: Path, spec: str, ts: datetime, *, ablated: list[str], skills: list[str]
) -> Path:
    results = RunResults(
        run=RunMeta(
            spec=spec,
            timestamp=ts,
            k=1,
            backend="claude-code",
            era=ERA_INSTALL_AND_DISCOVER,
            ablated=ablated,
        ),
        skill_snapshots=[
            SkillSnapshot(name=n, path=f"/x/{n}/SKILL.md") for n in skills
        ],
        task_results=[
            TaskResult(
                task_id="task-001",
                task_name="alpha",
                attempts=[
                    AttemptRecord(
                        attempt=1, output="", duration_seconds=1.0, outcome=Outcome.PASS
                    )
                ],
                successes=1,
                unusable=0,
                pass_at_k=1.0,
            )
        ],
        aggregate=AggregateScore(avg_score=1.0, scored_tasks=1, per_task=[]),
    )
    out_dir = root / ".caliper" / "results" / spec
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{ts.strftime('%Y-%m-%dT%H-%M-%SZ')}.json"
    path.write_text(json.dumps(results.model_dump(mode="json")))
    return path


def _two_runs(root: Path) -> tuple[Path, Path]:
    """An ablation pair: the control arm, then the full run, same spec."""
    ablated = _write_run(
        root,
        "demo",
        datetime(2026, 8, 4, 10, 0, tzinfo=timezone.utc),
        ablated=["my-skill"],
        skills=["keeper"],
    )
    full = _write_run(
        root,
        "demo",
        datetime(2026, 8, 4, 10, 30, tzinfo=timezone.utc),
        ablated=[],
        skills=["keeper", "my-skill"],
    )
    return ablated, full


def test_one_spec_name_twice_is_refused(monkeypatch, tmp_path) -> None:
    # Both sides take the same latest-run tie-break, so this is a run diffed
    # against itself: every delta zero, no guard tripped, nothing to suspect.
    _two_runs(tmp_path)
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["compare", "demo", "demo"])
    assert result.exit_code == 1
    assert "same run" in result.output
    assert "2026-08-04T10-30-00Z" in result.output


def test_two_spellings_of_one_file_are_refused(monkeypatch, tmp_path) -> None:
    # The check is on the resolved path, not the argument string: a spec name
    # and an explicit path to the run it resolves to are the same file.
    _, full = _two_runs(tmp_path)
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["compare", str(full), "demo"])
    assert result.exit_code == 1
    assert "same run" in result.output


def test_an_ablation_pair_addressed_by_path_compares(monkeypatch, tmp_path) -> None:
    # The workflow the refusal is protecting: control arm by path, latest by name.
    ablated, _ = _two_runs(tmp_path)
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["compare", str(ablated), "demo"])
    assert result.exit_code == 0, result.output
    assert "without my-skill" in result.output
    assert "full neighbourhood" in result.output


def test_two_distinct_runs_of_one_spec_still_compare(monkeypatch, tmp_path) -> None:
    # Same skill over time is a documented use (docs/CONTEXT.md → Run
    # comparison); only the *same file twice* is refused.
    ablated, full = _two_runs(tmp_path)
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["compare", str(ablated), str(full)])
    assert result.exit_code == 0, result.output
