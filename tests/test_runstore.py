"""The one module that knows where a run lives on disk."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from caliper.runstore import RunStore, UnreadableRun
from caliper.schema.results import (
    AggregateScore,
    AttemptRecord,
    Outcome,
    RunMeta,
    RunResults,
    TaskResult,
    TokenUsage,
    TranscriptTurn,
)


def _results(spec: str = "my-skill", *, at: datetime | None = None) -> RunResults:
    return RunResults(
        run=RunMeta(
            spec=spec,
            timestamp=at or datetime(2026, 8, 29, 10, 30, 0, tzinfo=timezone.utc),
            k=3,
            backend="claude-code",
        ),
        task_results=[],
        aggregate=AggregateScore(avg_score=0.0, per_task=[]),
    )


# --- saving ------------------------------------------------------------------


def test_save_round_trips_through_load(tmp_path) -> None:
    store = RunStore(tmp_path)
    results = _results()
    results.run.model = "claude-sonnet-5"
    results.run.interrupted = True
    results.run.mcp_servers = ["wiki"]
    results.task_results = [
        TaskResult(
            task_id="task-007",
            task_name="Read the wiki",
            expect="names the sections",
            activation_expected=["research"],
            attempts=[
                AttemptRecord(
                    attempt=2,
                    output="Found intro",
                    duration_seconds=3.25,
                    outcome=Outcome.TASK_FAIL,
                    assert_evidence="missing usage section",
                    activated=["research"],
                    activation_passed=True,
                    usage=TokenUsage(input_tokens=17, output_tokens=5),
                    transcript=[
                        TranscriptTurn(
                            role="tool_use",
                            content="read wiki",
                            tool_name="mcp__wiki__read",
                            tool_input={"repo": "caliper"},
                        ),
                        TranscriptTurn(
                            role="tool_result",
                            content="intro",
                            tool_name="mcp__wiki__read",
                            tool_output="intro",
                        ),
                    ],
                )
            ],
        )
    ]
    results.aggregate = AggregateScore.from_task_results(results.task_results, k=3)
    saved = store.save(results)

    loaded = store.load(saved)
    assert loaded.run.spec == "my-skill"
    assert loaded.run.timestamp == datetime(2026, 8, 29, 10, 30, tzinfo=timezone.utc)
    assert (loaded.run.k, loaded.run.backend, loaded.run.model) == (
        3,
        "claude-code",
        "claude-sonnet-5",
    )
    assert loaded.run.interrupted is True
    assert loaded.run.mcp_servers == ["wiki"]
    assert len(loaded.task_results) == 1
    task = loaded.task_results[0]
    assert (task.task_id, task.task_name, task.expect) == (
        "task-007",
        "Read the wiki",
        "names the sections",
    )
    assert task.activation_expected == ["research"]
    assert len(task.attempts) == 1
    attempt = task.attempts[0]
    assert (
        attempt.attempt,
        attempt.output,
        attempt.duration_seconds,
        attempt.outcome,
    ) == (2, "Found intro", 3.25, Outcome.TASK_FAIL)
    assert attempt.assert_evidence == "missing usage section"
    assert attempt.activated == ["research"]
    assert attempt.activation_passed is True
    assert attempt.usage == TokenUsage(input_tokens=17, output_tokens=5)
    assert attempt.transcript == [
        TranscriptTurn(
            role="tool_use",
            content="read wiki",
            tool_name="mcp__wiki__read",
            tool_input={"repo": "caliper"},
        ),
        TranscriptTurn(
            role="tool_result",
            content="intro",
            tool_name="mcp__wiki__read",
            tool_output="intro",
        ),
    ]
    assert loaded.aggregate.avg_score == 0.0
    assert loaded.aggregate.scored_tasks == 1
    assert loaded.aggregate.avg_activation_score == 1.0
    assert loaded.aggregate.per_task[0].score == 0.0


@pytest.mark.parametrize("spec", ["", ".", "..", "../outside", "a/b"])
def test_save_refuses_a_spec_name_that_is_not_one_directory(tmp_path, spec) -> None:
    """Such a name files the run beside, or outside, every spec's directory."""
    with pytest.raises(ValueError, match="spec name"):
        RunStore(tmp_path).save(_results(spec))

    assert not (tmp_path / ".caliper").exists()


def test_save_files_a_run_under_its_spec(tmp_path) -> None:
    store = RunStore(tmp_path)
    saved = store.save(_results("my-skill"))

    assert saved.parent == tmp_path / ".caliper" / "results" / "my-skill"


def test_save_names_the_file_after_the_run_timestamp(tmp_path) -> None:
    """The stem is the run id a caller passes back to ``--run`` and ``compare``."""
    store = RunStore(tmp_path)
    saved = store.save(_results(at=datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)))

    assert saved.stem == "2026-01-02T03-04-05Z"


def test_two_runs_of_one_spec_sit_side_by_side(tmp_path) -> None:
    store = RunStore(tmp_path)
    first = store.save(_results(at=datetime(2026, 1, 1, tzinfo=timezone.utc)))
    second = store.save(_results(at=datetime(2026, 1, 2, tzinfo=timezone.utc)))

    assert store.runs("my-skill") == [first, second]


def test_two_runs_in_the_same_second_both_survive(tmp_path) -> None:
    store = RunStore(tmp_path)
    first_results = _results()
    first_results.run.model = "first-model"
    second_results = _results()
    second_results.run.model = "second-model"
    for results, output in (
        (first_results, "first output"),
        (second_results, "second output"),
    ):
        results.task_results = [
            TaskResult(
                task_id="task-001",
                task_name="One",
                attempts=[
                    AttemptRecord(
                        attempt=1,
                        output=output,
                        duration_seconds=1.0,
                        outcome=Outcome.PASS,
                    )
                ],
            )
        ]
        results.aggregate = AggregateScore.from_task_results(results.task_results, k=3)
    first = store.save(first_results)
    second = store.save(second_results)

    assert first != second
    assert first.exists()
    assert second.exists()
    assert second.stem == f"{first.stem}-2"
    assert store.load(first).run.model == "first-model"
    assert store.load(second).run.model == "second-model"
    assert store.load(first).task_results[0].attempts[0].output == "first output"
    assert store.load(second).task_results[0].attempts[0].output == "second output"


def test_same_second_runs_are_listed_in_the_order_they_were_saved(tmp_path) -> None:
    """``Z-2`` sorts before ``Z`` as a file name; run order must not."""
    store = RunStore(tmp_path)
    saved = [store.save(_results()) for _ in range(11)]
    later = store.save(
        _results(at=datetime(2026, 8, 29, 10, 30, 1, tzinfo=timezone.utc))
    )

    assert store.runs("my-skill") == [*saved, later]


def test_latest_is_the_last_of_several_same_second_runs(tmp_path) -> None:
    store = RunStore(tmp_path)
    store.save(_results())
    last = store.save(_results())

    assert store.resolve("my-skill") == last


def test_a_disambiguated_run_resolves_by_its_id(tmp_path) -> None:
    store = RunStore(tmp_path)
    store.save(_results())
    second = store.save(_results())

    assert store.resolve("my-skill", run=second.stem) == second


# --- resolving a reference ---------------------------------------------------


def test_a_json_path_resolves_to_itself(tmp_path) -> None:
    store = RunStore(tmp_path)
    saved = store.save(_results())

    assert store.resolve(str(saved)) == saved


def test_a_json_path_that_does_not_exist_resolves_to_nothing(tmp_path) -> None:
    assert RunStore(tmp_path).resolve(str(tmp_path / "gone.json")) is None


def test_a_bare_spec_name_resolves_to_its_latest_run(tmp_path) -> None:
    """Latest is lexicographically last — the run id is an ISO timestamp."""
    store = RunStore(tmp_path)
    store.save(_results(at=datetime(2026, 1, 1, tzinfo=timezone.utc)))
    newest = store.save(_results(at=datetime(2026, 3, 1, tzinfo=timezone.utc)))
    store.save(_results(at=datetime(2026, 2, 1, tzinfo=timezone.utc)))

    assert store.resolve("my-skill") == newest


def test_a_named_run_resolves_to_that_run(tmp_path) -> None:
    store = RunStore(tmp_path)
    store.save(_results(at=datetime(2026, 3, 1, tzinfo=timezone.utc)))
    wanted = store.save(_results(at=datetime(2026, 1, 1, tzinfo=timezone.utc)))

    assert store.resolve("my-skill", run="2026-01-01T00-00-00Z") == wanted


def test_a_named_run_that_is_not_there_resolves_to_nothing(tmp_path) -> None:
    store = RunStore(tmp_path)
    store.save(_results())

    assert store.resolve("my-skill", run="2026-01-01T00-00-00Z") is None


def test_an_unknown_spec_resolves_to_nothing(tmp_path) -> None:
    assert RunStore(tmp_path).resolve("never-run") is None


def test_a_spec_with_no_runs_resolves_to_nothing(tmp_path) -> None:
    store = RunStore(tmp_path)
    store.spec_dir("empty").mkdir(parents=True)

    assert store.resolve("empty") is None


# --- enumerating -------------------------------------------------------------


def test_specs_lists_only_directories_that_hold_runs(tmp_path) -> None:
    store = RunStore(tmp_path)
    store.save(_results("beta"))
    store.save(_results("alpha"))
    store.spec_dir("empty").mkdir(parents=True)
    (store.results_dir / "stray.json").write_text("{}")

    assert store.specs() == ["alpha", "beta"]


def test_specs_is_empty_before_any_run(tmp_path) -> None:
    assert RunStore(tmp_path).specs() == []


def test_runs_is_empty_for_an_unknown_spec(tmp_path) -> None:
    assert RunStore(tmp_path).runs("never-run") == []


# --- unreadable runs ---------------------------------------------------------


def test_load_names_the_file_it_could_not_read(tmp_path) -> None:
    """A corrupt run file is a case, not a bare ``except Exception``.

    The listing shows one row per run file and must survive a bad one; naming
    the path is what turns "?" into something the reader can go and look at.
    """
    store = RunStore(tmp_path)
    bad = store.spec_dir("my-skill") / "2026-01-01T00-00-00Z.json"
    bad.parent.mkdir(parents=True)
    bad.write_text("not json at all")

    with pytest.raises(UnreadableRun) as caught:
        store.load(bad)

    assert str(bad) in str(caught.value)


def test_load_rejects_json_that_is_not_a_run(tmp_path) -> None:
    store = RunStore(tmp_path)
    bad = tmp_path / "other.json"
    bad.write_text('{"hello": "world"}')

    with pytest.raises(UnreadableRun):
        store.load(bad)


# --- discovery ---------------------------------------------------------------


def test_discover_finds_the_nearest_root(monkeypatch, tmp_path) -> None:
    """Nearest wins, so a package given its own root keeps its own history."""
    (tmp_path / ".git").mkdir()
    package = tmp_path / "packages" / "foo"
    (package / ".caliper").mkdir(parents=True)
    (tmp_path / ".caliper").mkdir()
    monkeypatch.chdir(package)

    assert RunStore.discover().root == package.resolve()


def test_discover_walks_up_to_an_existing_root(monkeypatch, tmp_path) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / ".caliper").mkdir()
    deep = tmp_path / "evals" / "nested"
    deep.mkdir(parents=True)
    monkeypatch.chdir(deep)

    assert RunStore.discover().root == tmp_path.resolve()


def test_discover_names_the_repo_root_when_no_root_exists(monkeypatch, tmp_path):
    """The first run of a project must not plant its root wherever you stood.

    Rooting at the working directory would make the very first `caliper run`
    decide the layout, and a later run from elsewhere in the same project would
    silently create a second root (docs/adr/0022).
    """
    (tmp_path / ".git").mkdir()
    spec_dir = tmp_path / "evals"
    spec_dir.mkdir()
    monkeypatch.chdir(spec_dir)

    store = RunStore.discover()

    assert store.root == tmp_path.resolve()
    # Named, not created: `save` is what writes.
    assert not store.caliper_dir.exists()


def test_discover_does_not_reach_past_the_repo_boundary(monkeypatch, tmp_path) -> None:
    """A stray `.caliper/` above the repo must not become the repo's store."""
    (tmp_path / ".caliper").mkdir()
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    monkeypatch.chdir(repo)

    assert RunStore.discover().root == repo.resolve()


def test_discover_falls_back_to_the_working_directory_outside_a_repo(
    monkeypatch, tmp_path
) -> None:
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)

    # tmp_path itself has no .git and no .caliper, so the walk finds nothing and
    # the working directory stands in for a project root.
    assert RunStore.discover(work).root == work.resolve()


def test_discover_accepts_an_explicit_start(tmp_path) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / ".caliper").mkdir()
    deep = tmp_path / "a" / "b"
    deep.mkdir(parents=True)

    assert RunStore.discover(deep).root == tmp_path.resolve()


def test_a_spec_path_resolves_like_its_spec_name(tmp_path) -> None:
    store = RunStore(tmp_path)
    saved = store.save(_results("my-skill"))

    assert store.resolve("evals/my-skill.eval.yaml") == saved


def test_a_file_that_is_not_a_results_file_says_so(tmp_path) -> None:
    other = tmp_path / "hosts"
    other.write_text("127.0.0.1 localhost\n")
    store = RunStore(tmp_path)
    store.save(_results())

    assert store.resolve(str(other)) is None
    assert "is not a results file" in store.no_results(str(other))


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("aggregate", "avg_score"), 2.5),
        (("aggregate", "avg_score"), float("nan")),
        (("aggregate", "avg_score"), -0.1),
        (("aggregate", "scored_tasks"), -1),
        (("run", "k"), 0),
    ],
)
def test_a_run_holding_an_impossible_number_is_unreadable(tmp_path, path, value):
    """A 250% score would otherwise render as a figure, and NaN crash rounding."""
    import json

    saved = RunStore(tmp_path).save(_results())
    data = json.loads(saved.read_text())
    data[path[0]][path[1]] = value
    saved.write_text(json.dumps(data))

    with pytest.raises(UnreadableRun):
        RunStore.load(saved)
