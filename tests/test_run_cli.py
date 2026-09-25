from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from typer.testing import CliRunner

from caliper.main import app
from caliper.reporter import print_results
from caliper.runstore import RunStore
from caliper.schema.results import (
    AggregateScore,
    AttemptRecord,
    HookFailure,
    Outcome,
    RunMeta,
    RunResults,
    TaskResult,
)

from conftest import StubHarness


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
        "caliper.commands.run.get_harness", lambda *args, **kwargs: StubHarness()
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
    # Omitted: None, so the runner follows the spec's own default.
    assert calls["user_customizations"] is None
    # A requested/reported model mismatch has somewhere to surface (#131).
    assert callable(calls["on_warning"])


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
                backend=harness_args["backend"],
                model=harness_args["model"],
            ),
            skill_snapshots=[],
            task_results=[],
            aggregate=AggregateScore(avg_score=0.0, per_task=[]),
        )

    def fake_get_harness(backend, model):
        harness_args["backend"], harness_args["model"] = backend, model
        return StubHarness()

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

    monkeypatch.setattr(
        "caliper.commands.run.get_harness", lambda *a, **k: StubHarness()
    )
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
    monkeypatch.setattr(
        "caliper.commands.run.get_harness", lambda *a, **k: StubHarness()
    )
    monkeypatch.setattr("caliper.commands.run.EvalJudge", lambda *a, **k: object())
    monkeypatch.setattr(
        "caliper.commands.run.make_progress", lambda *a, **k: (_Progress(), {})
    )
    monkeypatch.setattr("caliper.commands.run.update_progress", lambda *a, **k: None)
    monkeypatch.setattr("caliper.commands.run.print_banner", lambda *a, **k: None)
    monkeypatch.setattr("caliper.commands.run.print_results", lambda *a, **k: None)
    monkeypatch.setattr("caliper.commands.run.run", lambda **kwargs: finished)


@pytest.mark.parametrize(
    "argv, spec_prefix, forwarded, shown, hidden",
    [
        # The default: one quiet line saying how to isolate, no warning.
        ([], "", None, "without asking", "⚠"),
        (["--user-customizations"], "", True, "⚠ --user-customizations", None),
        (["--no-user-customizations"], "", False, None, "account connectors"),
        # A spec that turns it on gets the full warning, naming itself.
        ([], "user_customizations: true\n", None, "(spec)", None),
    ],
)
def test_run_cli_user_customizations_notice(
    monkeypatch, tmp_path, argv, spec_prefix, forwarded, shown, hidden
) -> None:
    spec_file = tmp_path / "sample.eval.yaml"
    spec_file.write_text(
        spec_prefix
        + "tasks:\n  - name: One\n    prompt: Do it\n    assert: assert True\n"
    )
    calls = {}
    finished = _finished(datetime(2026, 7, 3, tzinfo=timezone.utc))
    _stub_a_run(monkeypatch, finished)
    monkeypatch.setattr(
        "caliper.commands.run.get_harness",
        lambda *a, **k: StubHarness(supports_mcp=True),
    )
    monkeypatch.setattr(
        "caliper.commands.run.run", lambda **kw: calls.update(kw) or finished
    )
    monkeypatch.setattr("caliper.commands.run._save_and_report", lambda *a, **k: None)

    result = runner.invoke(app, ["run", str(spec_file), *argv])

    assert result.exit_code == 0, result.output
    assert calls["user_customizations"] is forwarded
    if shown:
        assert shown in result.output
    if hidden:
        assert hidden not in result.output


def test_run_cli_exits_two_after_cleanup_failure(monkeypatch, tmp_path) -> None:
    finished = _finished(datetime(2026, 7, 3, tzinfo=timezone.utc))
    failure = HookFailure(
        task_id="task-001",
        attempt=1,
        phase="cleanup",
        exit_code=9,
        output="cleanup broke",
    )
    finished.run.hook_failures = [failure]
    finished.task_results[0].attempts[0].hook_failures = [failure]
    _stub_a_run(monkeypatch, finished)
    saved = []
    monkeypatch.setattr(
        "caliper.commands.run._save_and_report",
        lambda *args, **kwargs: saved.append(args),
    )
    spec_file = tmp_path / "sample.eval.yaml"
    spec_file.write_text(
        "tasks:\n  - name: One\n    prompt: Do it\n    assert: assert True\n"
    )

    result = runner.invoke(app, ["run", str(spec_file), "--k", "1"])

    assert result.exit_code == 2
    assert len(saved) == 1
    assert saved[0][0].task_results[0].attempts[0].outcome is Outcome.PASS


def _with_outcomes(*outcomes: Outcome) -> RunResults:
    finished = _finished(datetime(2026, 7, 3, tzinfo=timezone.utc))
    finished.task_results[0].attempts = [
        AttemptRecord(attempt=i, output="", duration_seconds=0.1, outcome=outcome)
        for i, outcome in enumerate(outcomes, start=1)
    ]
    return finished


def _invoke_run(monkeypatch, tmp_path, finished: RunResults):
    _stub_a_run(monkeypatch, finished)
    saved = []
    monkeypatch.setattr(
        "caliper.commands.run._save_and_report",
        lambda *args, **kwargs: saved.append(args),
    )
    spec_file = tmp_path / "sample.eval.yaml"
    spec_file.write_text(
        "tasks:\n  - name: One\n    prompt: Do it\n    assert: assert True\n"
    )
    return runner.invoke(app, ["run", str(spec_file), "--k", "3"]), saved


def test_run_cli_exits_two_when_no_attempt_was_usable(monkeypatch, tmp_path) -> None:
    finished = _with_outcomes(Outcome.INFRA_ERROR, Outcome.INFRA_ERROR, Outcome.TIMEOUT)

    result, saved = _invoke_run(monkeypatch, tmp_path, finished)

    assert result.exit_code == 2, result.output
    assert len(saved) == 1
    assert "2 infra_error" in result.output
    assert "1 timeout" in result.output


def test_run_cli_exits_zero_when_some_attempt_was_usable(monkeypatch, tmp_path) -> None:
    finished = _with_outcomes(Outcome.INFRA_ERROR, Outcome.JUDGE_ERROR, Outcome.PASS)

    result, _ = _invoke_run(monkeypatch, tmp_path, finished)

    assert result.exit_code == 0, result.output


def test_run_cli_exits_zero_for_a_trigger_probe_run(monkeypatch, tmp_path) -> None:
    # NOT_CHECKED is unusable but not noise: nothing went wrong.
    finished = _with_outcomes(Outcome.NOT_CHECKED, Outcome.NOT_CHECKED)

    result, _ = _invoke_run(monkeypatch, tmp_path, finished)

    assert result.exit_code == 0, result.output


def test_report_highlights_cleanup_failure_on_passing_attempt(capfd) -> None:
    finished = _finished(datetime(2026, 7, 3, tzinfo=timezone.utc))
    failure = HookFailure(
        task_id="task-001",
        attempt=1,
        phase="cleanup",
        exit_code=9,
        output="cleanup broke",
    )
    finished.run.hook_failures = [failure]
    finished.task_results[0].attempts[0].hook_failures = [failure]

    print_results(finished)

    output = capfd.readouterr().out
    assert "lifecycle hooks failed" in output
    assert "cleanup exited 9" in output
    assert "cleanup broke" in output
    assert "HOOK ERROR" in output


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


def test_run_cli_writes_output_to_a_new_directory(monkeypatch, tmp_path) -> None:
    spec_dir = _project(tmp_path)
    finished = _finished(datetime(2026, 7, 3, 12, 30, tzinfo=timezone.utc))
    _stub_a_run(monkeypatch, finished)
    monkeypatch.setattr("caliper.commands.run.print_results", print_results)
    monkeypatch.chdir(tmp_path)
    output = Path("some/new/dir/out.json")

    result = runner.invoke(
        app, ["run", str(spec_dir / "sample.eval.yaml"), "--output", str(output)]
    )

    assert result.exit_code == 0, result.output
    assert RunResults.model_validate_json(output.read_text()) == finished
    assert "Score" in result.output
    assert "Results saved to" in result.output


def test_run_cli_keeps_report_and_interrupt_exit_if_output_cannot_be_written(
    monkeypatch, tmp_path
) -> None:
    spec_dir = _project(tmp_path)
    finished = _finished(datetime(2026, 7, 3, 12, 30, tzinfo=timezone.utc))
    finished.run.interrupted = True
    _stub_a_run(monkeypatch, finished)
    monkeypatch.setattr("caliper.commands.run.print_results", print_results)
    monkeypatch.chdir(tmp_path)
    output = tmp_path / "existing-directory"
    output.mkdir()

    result = runner.invoke(
        app, ["run", str(spec_dir / "sample.eval.yaml"), "--output", str(output)]
    )

    assert result.exit_code == 130, result.output
    assert "Could not write --output" in result.output
    assert "Score" in result.output
    assert "Results saved to" in result.output
    saved = RunStore(tmp_path).resolve("sample")
    assert saved is not None
    assert RunResults.model_validate_json(saved.read_text()).run.interrupted


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


def test_run_cli_rejects_fewer_than_one_attempt(monkeypatch, tmp_path) -> None:
    # k=0 schedules nothing, so the run would measure nothing and exit 0.
    spec_file = tmp_path / "sample.eval.yaml"
    spec_file.write_text("tasks:\n  - name: One\n    prompt: Do it\n    expect: Done\n")
    for k in ("0", "-1"):
        result = runner.invoke(app, ["run", str(spec_file), "--k", k])

        assert result.exit_code == 1, result.output
        assert "--k" in result.output


def test_run_cli_refuses_unknown_backends_before_any_attempt(
    monkeypatch, tmp_path
) -> None:
    """A misspelt backend is a diagnosis panel and exit 2, not a traceback."""
    spec_file = tmp_path / "sample.eval.yaml"
    spec_file.write_text(
        "skills:\n  - ./SKILL.md\n"
        "tasks:\n  - name: One\n    prompt: Do it\n    assert: assert True\n"
    )

    def fake_run(**kwargs):
        raise AssertionError("no attempt should run")

    monkeypatch.setattr("caliper.commands.run.run", fake_run)

    for flag, target in (("--model", "foo:bar"), ("--judge-model", "bogus:x")):
        result = runner.invoke(app, ["run", str(spec_file), flag, target])

        assert result.exit_code == 2, result.output
        assert "Traceback" not in result.output
        assert flag in result.output
        # Names what is wrong and what would be right.
        assert "Unknown backend" in result.output
        assert "claude-code" in result.output and "codex" in result.output


@pytest.mark.parametrize(
    "flag, value",
    [("--k", "0"), ("--workers", "0"), ("--timeout", "0"), ("--timeout", "-5")],
)
def test_run_rejects_a_value_below_one_before_running(
    monkeypatch, tmp_path, flag, value
) -> None:
    import caliper.commands.run as run_module

    def never_run(**_: object):
        raise AssertionError("run() should not be reached")

    monkeypatch.setattr(run_module, "run", never_run)
    spec = tmp_path / "s.eval.yaml"
    spec.write_text("tasks:\n  - {name: t, prompt: p, assert: 'assert True'}\n")

    result = runner.invoke(app, ["run", str(spec), flag, value])

    assert result.exit_code == 1
    assert f"{flag} must be at least 1" in result.output
