from __future__ import annotations

from datetime import datetime, timezone

from typer.testing import CliRunner

from caliper.main import app
from caliper.runner import AttemptEvent
from caliper.schema.results import (
    AggregateScore,
    AttemptRecord,
    Outcome,
    RunMeta,
    RunResults,
    SkillSnapshot,
    TaskResult,
)


runner = CliRunner()


class _Console:
    def print(self, *args, **kwargs):
        return None


class _Progress:
    console = _Console()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def test_run_cli_passes_fail_fast_unusable(monkeypatch, tmp_path) -> None:
    spec_file = tmp_path / "sample.eval.yaml"
    spec_file.write_text(
        """
skill:
  backend: codex
tasks:
  - name: One
    prompt: Do it
    assert: assert True
"""
    )
    calls = {}

    def fake_run(**kwargs):
        calls.update(kwargs)
        return RunResults(
            run=RunMeta(
                spec="sample",
                timestamp=datetime(2026, 7, 3, tzinfo=timezone.utc),
                k=kwargs["k"],
                backend="codex",
            ),
            skill_snapshot=SkillSnapshot(path=""),
            task_results=[],
            aggregate=AggregateScore(avg_pass_at_k=0.0, per_task=[]),
        )

    monkeypatch.setattr(
        "caliper.commands.run.get_harness", lambda *args, **kwargs: object()
    )
    monkeypatch.setattr(
        "caliper.commands.run.EvalJudge", lambda *args, **kwargs: object()
    )
    monkeypatch.setattr(
        "caliper.commands.run.make_progress", lambda *args, **kwargs: (_Progress(), {})
    )
    monkeypatch.setattr(
        "caliper.commands.run.update_progress", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        "caliper.commands.run.print_banner", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        "caliper.commands.run.print_results", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        "caliper.commands.run.save_results",
        lambda *args, **kwargs: tmp_path / "results.json",
    )
    monkeypatch.setattr("caliper.commands.run.run", fake_run)

    result = runner.invoke(app, ["run", str(spec_file), "--k", "3", "--fail-fast", "2"])

    assert result.exit_code == 0, result.output
    assert calls["fail_fast_unusable"] == 2


def test_run_cli_repaints_early_stopped_task_as_finished(
    monkeypatch, tmp_path
) -> None:
    spec_file = tmp_path / "sample.eval.yaml"
    spec_file.write_text(
        """
skill:
  backend: codex
tasks:
  - name: One
    prompt: Do it
    assert: assert True
"""
    )
    progress_updates = []

    def stopped_task() -> TaskResult:
        return TaskResult(
            task_id="task-001",
            task_name="One",
            attempts=[
                AttemptRecord(
                    attempt=1,
                    output="",
                    duration_seconds=0.1,
                    outcome=Outcome.INFRA_ERROR,
                    assert_evidence="agent failed",
                )
            ],
            successes=0,
            unusable=1,
            pass_at_k=None,
        )

    def fake_run(**kwargs):
        kwargs["on_attempt_done"](
            AttemptEvent(task_id="task-001", attempt=1, outcome=Outcome.INFRA_ERROR)
        )
        kwargs["on_task_done"](stopped_task())
        return RunResults(
            run=RunMeta(
                spec="sample",
                timestamp=datetime(2026, 7, 3, tzinfo=timezone.utc),
                k=kwargs["k"],
                backend="codex",
            ),
            skill_snapshot=SkillSnapshot(path=""),
            task_results=[stopped_task()],
            aggregate=AggregateScore(avg_pass_at_k=0.0, per_task=[]),
        )

    def fake_update_progress(*args, **kwargs):
        progress_updates.append(kwargs)

    monkeypatch.setattr(
        "caliper.commands.run.get_harness", lambda *args, **kwargs: object()
    )
    monkeypatch.setattr(
        "caliper.commands.run.EvalJudge", lambda *args, **kwargs: object()
    )
    monkeypatch.setattr(
        "caliper.commands.run.make_progress", lambda *args, **kwargs: (_Progress(), {})
    )
    monkeypatch.setattr("caliper.commands.run.update_progress", fake_update_progress)
    monkeypatch.setattr(
        "caliper.commands.run.print_banner", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        "caliper.commands.run.print_results", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        "caliper.commands.run.save_results",
        lambda *args, **kwargs: tmp_path / "results.json",
    )
    monkeypatch.setattr("caliper.commands.run.run", fake_run)

    result = runner.invoke(app, ["run", str(spec_file), "--k", "3", "--fail-fast", "1"])

    assert result.exit_code == 0, result.output
    assert any(update.get("finished") for update in progress_updates)
