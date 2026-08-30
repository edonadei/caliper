from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from typer.testing import CliRunner

from caliper.main import app
from caliper.runstore import RunStore
from caliper.schema.results import (
    AggregateScore,
    AttemptRecord,
    Outcome,
    RunMeta,
    RunResults,
    TaskResult,
)


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
skills:
  - ./SKILL.md
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
            skill_snapshots=[],
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
    assert calls["ablate"] == []
    # Engine is resolved at the run seam and defaults to claude-code (ADR 0004)
    assert calls["backend"] == "claude-code"
    assert calls["model"] is None
    assert calls["judge_backend"] == "claude-code"
    assert calls["judge_model"] is None


def test_run_cli_resolves_backend_and_judge_model_targets(
    monkeypatch, tmp_path
) -> None:
    """--model and --judge-model accept a backend:model compound and split it."""
    spec_file = tmp_path / "sample.eval.yaml"
    spec_file.write_text(
        "skills:\n  - ./SKILL.md\n"
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
            skill_snapshots=[],
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


def test_run_cli_collects_repeated_ablate_flags(monkeypatch, tmp_path) -> None:
    """--ablate is repeatable; naming every skill is how you get the bare agent."""
    spec_file = tmp_path / "sample.eval.yaml"
    spec_file.write_text(
        "skills:\n  - ./SKILL.md\n"
        "tasks:\n  - name: One\n    prompt: Do it\n    assert: assert True\n"
    )
    calls = {}

    def fake_run(**kwargs):
        calls.update(kwargs)
        return RunResults(
            run=RunMeta(
                spec="sample",
                timestamp=datetime(2026, 7, 3, tzinfo=timezone.utc),
                k=kwargs["k"],
                backend="claude-code",
            ),
            skill_snapshots=[],
            task_results=[],
            aggregate=AggregateScore(avg_score=0.0, per_task=[]),
        )

    monkeypatch.setattr("caliper.commands.run.get_harness", lambda *a, **k: object())
    monkeypatch.setattr("caliper.commands.run.EvalJudge", lambda *a, **k: object())
    monkeypatch.setattr(
        "caliper.commands.run.make_progress", lambda *a, **k: (_Progress(), {})
    )
    monkeypatch.setattr("caliper.commands.run.update_progress", lambda *a, **k: None)
    monkeypatch.setattr("caliper.commands.run.print_banner", lambda *a, **k: None)
    monkeypatch.setattr("caliper.commands.run.print_results", lambda *a, **k: None)
    monkeypatch.setattr("caliper.commands.run.run", fake_run)

    result = runner.invoke(
        app, ["run", str(spec_file), "--ablate", "grilling", "--ablate", "docs"]
    )

    assert result.exit_code == 0, result.output
    assert calls["ablate"] == ["grilling", "docs"]


def _finished(timestamp: datetime) -> RunResults:
    """A one-attempt run: a run where nothing ran is deliberately not saved."""
    return RunResults(
        run=RunMeta(
            spec="sample",
            timestamp=timestamp,
            k=1,
            backend="claude-code",
        ),
        skill_snapshots=[],
        task_results=[
            TaskResult(
                task_id="task-001",
                task_name="One",
                attempts=[
                    AttemptRecord(
                        attempt=1,
                        output="done",
                        duration_seconds=0.1,
                        outcome=Outcome.PASS,
                    )
                ],
            )
        ],
        aggregate=AggregateScore(avg_score=1.0, per_task=[]),
    )


def _stub_a_run(monkeypatch, finished: RunResults) -> None:
    monkeypatch.setattr("caliper.commands.run.get_harness", lambda *a, **k: object())
    monkeypatch.setattr("caliper.commands.run.EvalJudge", lambda *a, **k: object())
    monkeypatch.setattr(
        "caliper.commands.run.make_progress", lambda *a, **k: (_Progress(), {})
    )
    monkeypatch.setattr("caliper.commands.run.update_progress", lambda *a, **k: None)
    monkeypatch.setattr("caliper.commands.run.print_banner", lambda *a, **k: None)
    monkeypatch.setattr("caliper.commands.run.print_results", lambda *a, **k: None)
    monkeypatch.setattr("caliper.commands.run.run", lambda **kwargs: finished)


def _project(root: Path) -> Path:
    """A git project with its spec in a subdirectory, and no results root yet."""
    (root / ".git").mkdir(parents=True)
    spec_dir = root / "evals"
    spec_dir.mkdir()
    (spec_dir / "sample.eval.yaml").write_text(
        "skills:\n  - ./SKILL.md\n"
        "tasks:\n  - name: One\n    prompt: Do it\n    assert: assert True\n"
    )
    return spec_dir


def test_run_cli_saves_the_run_at_the_discovered_root(monkeypatch, tmp_path) -> None:
    """`run` files the run at the results root, not beside the spec file.

    Where the file lands *within* a root is the store's business, but which root
    the CLI picks is the CLI's — and it has to be the root every reading command
    resolves, or a spec in a subdirectory files its runs where `report` never
    looks (docs/adr/0022).
    """
    spec_dir = _project(tmp_path)
    _stub_a_run(
        monkeypatch, _finished(datetime(2026, 7, 3, 12, 30, tzinfo=timezone.utc))
    )
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["run", "evals/sample.eval.yaml"])

    assert result.exit_code == 0, result.output
    assert not (spec_dir / ".caliper").exists(), "the run was filed beside its spec"
    saved = RunStore(tmp_path).resolve("sample")
    assert saved is not None, "the run was not filed at the project's results root"


def test_a_run_is_findable_by_report_from_another_directory(
    monkeypatch, tmp_path
) -> None:
    """The bug this rooting exists to close, end to end through the CLI.

    `run` is invoked from the spec's *own* subdirectory — the case that used to
    file results into `evals/.caliper/` — and `report` is then invoked from the
    project root, addressing the run by spec name with no path in hand. Both
    commands discover the same root, so the second finds what the first wrote.
    """
    project = tmp_path / "project"
    spec_dir = _project(project)
    _stub_a_run(monkeypatch, _finished(datetime(2026, 7, 4, 9, 0, tzinfo=timezone.utc)))

    monkeypatch.chdir(spec_dir)
    run_result = runner.invoke(app, ["run", "sample.eval.yaml"])
    assert run_result.exit_code == 0, run_result.output

    monkeypatch.chdir(project)
    report_result = runner.invoke(app, ["report", "sample", "--format", "json"])
    assert report_result.exit_code == 0, report_result.output
    assert "2026-07-04T09:00:00" in report_result.output


def test_report_outside_the_project_does_not_find_its_runs(
    monkeypatch, tmp_path
) -> None:
    """Discovery walks up, never sideways, and stops at the repo boundary.

    The run belongs to `project/`; a sibling directory outside it must not reach
    in, and the message has to say so rather than reading like a typo.
    """
    project = tmp_path / "project"
    _project(project)
    _stub_a_run(monkeypatch, _finished(datetime(2026, 7, 5, 9, 0, tzinfo=timezone.utc)))

    monkeypatch.chdir(project)
    assert runner.invoke(app, ["run", "evals/sample.eval.yaml"]).exit_code == 0

    elsewhere = tmp_path / "elsewhere"
    (elsewhere / ".git").mkdir(parents=True)
    monkeypatch.chdir(elsewhere)

    result = runner.invoke(app, ["report", "sample"])

    assert result.exit_code == 1
    assert "No evaluation results" in result.output


def test_baseline_is_retired_and_says_where_the_capability_went(tmp_path) -> None:
    # Not remapped onto --ablate: --baseline ran two arms in one invocation, so
    # honouring the name over the new semantics would silently halve a scripted
    # caller's spend and stop rendering the delta it was reading (docs/adr/0015).
    spec_file = tmp_path / "sample.eval.yaml"
    spec_file.write_text(
        "skills:\n  - ./SKILL.md\n"
        "tasks:\n  - name: One\n    prompt: Do it\n    assert: assert True\n"
    )
    result = runner.invoke(app, ["run", str(spec_file), "--baseline"])
    assert result.exit_code == 2
    assert "--ablate" in result.output
    assert "caliper compare" in result.output
