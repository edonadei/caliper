"""The one module that knows where a run lives on disk."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from caliper.runstore import RunStore, UnreadableRun
from caliper.schema.results import AggregateScore, RunMeta, RunResults


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
    saved = store.save(_results())

    assert store.load(saved).run.spec == "my-skill"


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
