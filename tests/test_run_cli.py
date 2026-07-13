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
  path: ./SKILL.md
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
            aggregate=AggregateScore(avg_score=0.0, per_task=[]),
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
    # Engine is resolved at the run seam and defaults to claude-code (ADR 0004)
    assert calls["backend"] == "claude-code"
    assert calls["model"] is None
    assert calls["judge_backend"] == "claude-code"
    assert calls["judge_model"] == "claude-sonnet-4-6"


def test_run_cli_resolves_backend_and_judge_model_targets(
    monkeypatch, tmp_path
) -> None:
    """--model and --judge-model accept a backend:model compound and split it."""
    spec_file = tmp_path / "sample.eval.yaml"
    spec_file.write_text(
        "skill:\n  path: ./SKILL.md\n"
        "tasks:\n  - name: One\n    prompt: Do it\n    assert: assert True\n"
    )

    harness_args = {}
    judge_args = {}

    def fake_run(**kwargs):
        return RunResults(
            run=RunMeta(
                spec="sample",
                timestamp=datetime(2026, 7, 3, tzinfo=timezone.utc),
                k=kwargs["k"],
                backend=kwargs["backend"],
                model=kwargs["model"],
            ),
            skill_snapshot=SkillSnapshot(path=""),
            task_results=[],
            aggregate=AggregateScore(avg_score=0.0, per_task=[]),
        )

    def fake_get_harness(backend, model):
        harness_args["backend"], harness_args["model"] = backend, model
        return object()

    def fake_eval_judge(backend, model):
        judge_args["backend"], judge_args["model"] = backend, model
        return object()

    monkeypatch.setattr("caliper.commands.run.get_harness", fake_get_harness)
    monkeypatch.setattr("caliper.commands.run.EvalJudge", fake_eval_judge)
    monkeypatch.setattr(
        "caliper.commands.run.make_progress", lambda *a, **k: (_Progress(), {})
    )
    monkeypatch.setattr("caliper.commands.run.update_progress", lambda *a, **k: None)
    monkeypatch.setattr("caliper.commands.run.print_banner", lambda *a, **k: None)
    monkeypatch.setattr("caliper.commands.run.print_results", lambda *a, **k: None)
    monkeypatch.setattr(
        "caliper.commands.run.save_results", lambda *a, **k: tmp_path / "r.json"
    )
    monkeypatch.setattr("caliper.commands.run.run", fake_run)

    result = runner.invoke(
        app,
        [
            "run",
            str(spec_file),
            "--model",
            "codex:gpt-5-codex",
            "--judge-model",
            "pi:claude-sonnet-4-6",
        ],
    )

    assert result.exit_code == 0, result.output
    assert harness_args == {"backend": "codex", "model": "gpt-5-codex"}
    assert judge_args == {"backend": "pi", "model": "claude-sonnet-4-6"}
