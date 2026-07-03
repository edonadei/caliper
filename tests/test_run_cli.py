from __future__ import annotations

from datetime import datetime, timezone

from typer.testing import CliRunner

from caliper.main import app
from caliper.schema.results import AggregateScore, RunMeta, RunResults, SkillSnapshot


runner = CliRunner()


class _Progress:
    console = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def test_run_cli_forwards_options_to_run(monkeypatch, tmp_path) -> None:
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

    result = runner.invoke(
        app,
        ["run", str(spec_file), "--k", "3", "--workers", "2", "--fail-fast", "2"],
    )

    assert result.exit_code == 0, result.output
    # Explicitly passed flags reach run(...)
    assert calls["k"] == 3
    assert calls["workers"] == 2
    assert calls["fail_fast_unusable"] == 2
    # Defaults flow through unchanged when the flag is omitted
    assert calls["timeout"] == 120
    assert calls["baseline"] is False
