"""The attempt, not the task, is the unit of parallelism.

See docs/adr/0018-the-attempt-is-the-unit-of-parallelism.md. The concurrency
claims are asserted with a ``threading.Barrier``: if the attempts do not overlap
the barrier times out and the run fails loudly, rather than a timing assertion
that passes on a fast machine and flakes on a slow one.
"""

from __future__ import annotations

import threading

import pytest

from caliper.harness.base import AttemptResult, HarnessBackend
from caliper.judge.base import JudgeResult
from caliper.runner import run
from caliper.schema.results import Outcome
from caliper.schema.spec import EvalSpec, TaskSpec


class BarrierHarness(HarnessBackend):
    """Every attempt waits for ``parties`` of its peers before returning."""

    def __init__(self, parties: int) -> None:
        self._barrier = threading.Barrier(parties, timeout=10)

    @property
    def name(self) -> str:
        return "barrier"

    def run(self, task_id: str, attempt: int, prompt: str, **kwargs) -> AttemptResult:
        self._barrier.wait()
        return AttemptResult(
            task_id=task_id,
            attempt=attempt,
            transcript=[],
            final_output="done",
            exit_code=0,
            duration_seconds=0.01,
        )


class PerTaskConcurrencyProbe(HarnessBackend):
    """Records the highest number of attempts of one task running at once."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._live: dict[str, int] = {}
        self.peak_per_task = 0

    @property
    def name(self) -> str:
        return "probe"

    def run(self, task_id: str, attempt: int, prompt: str, **kwargs) -> AttemptResult:
        with self._lock:
            live = self._live.get(task_id, 0) + 1
            self._live[task_id] = live
            self.peak_per_task = max(self.peak_per_task, live)
        try:
            # Long enough that a second attempt of the same task would be seen
            # overlapping this one if the scheduler allowed it.
            threading.Event().wait(0.05)
            return AttemptResult(
                task_id=task_id,
                attempt=attempt,
                transcript=[],
                final_output="",
                exit_code=1,
                duration_seconds=0.01,
                error="agent failed",
            )
        finally:
            with self._lock:
                self._live[task_id] -= 1


class PassingJudge:
    def evaluate(self, task, transcript, final_output, spec_dir) -> JudgeResult:
        return JudgeResult(passed=True, reasoning="ok")


def _spec(n_tasks: int = 1) -> EvalSpec:
    return EvalSpec(
        tasks=[
            TaskSpec(
                id=f"task-{i + 1:03d}",
                name=f"Task {i + 1}",
                prompt="Do the thing",
                assert_script="assert True",
            )
            for i in range(n_tasks)
        ]
    )


def test_attempts_of_a_single_task_run_in_parallel(tmp_path) -> None:
    spec_path = tmp_path / "solo.eval.yaml"
    spec_path.write_text("tasks: []\n")
    # Four attempts of one task: nothing can get past the barrier unless all
    # four are in flight together. Before attempts were the unit of work this
    # spec was scheduled as one job and ran strictly serially.
    harness = BarrierHarness(parties=4)

    results = run(
        spec=_spec(),
        spec_path=spec_path,
        harness=harness,
        judge=PassingJudge(),
        k=4,
        workers=4,
        timeout=5,
    )

    assert [a.outcome for a in results.task_results[0].attempts] == [Outcome.PASS] * 4


def test_attempts_are_recorded_in_order_whatever_order_they_finish(tmp_path) -> None:
    spec_path = tmp_path / "solo.eval.yaml"
    spec_path.write_text("tasks: []\n")

    results = run(
        spec=_spec(),
        spec_path=spec_path,
        harness=BarrierHarness(parties=4),
        judge=PassingJudge(),
        k=4,
        workers=4,
        timeout=5,
    )

    assert [a.attempt for a in results.task_results[0].attempts] == [1, 2, 3, 4]


def test_workers_are_shared_across_tasks(tmp_path) -> None:
    spec_path = tmp_path / "many.eval.yaml"
    spec_path.write_text("tasks: []\n")
    # Two tasks × k=3 with six workers: all six attempts have to be in flight at
    # once, which per-task scheduling could never do — it capped concurrency at
    # the task count however high --workers went.
    results = run(
        spec=_spec(n_tasks=2),
        spec_path=spec_path,
        harness=BarrierHarness(parties=6),
        judge=PassingJudge(),
        k=3,
        workers=6,
        timeout=5,
    )

    assert sum(len(t.attempts) for t in results.task_results) == 6


def test_fail_fast_keeps_a_task_sequential(tmp_path) -> None:
    """The streak counts *consecutive* attempts, so fail-fast keeps its chain."""
    spec_path = tmp_path / "ff.eval.yaml"
    spec_path.write_text("tasks: []\n")
    harness = PerTaskConcurrencyProbe()

    run(
        spec=_spec(n_tasks=2),
        spec_path=spec_path,
        harness=harness,
        judge=PassingJudge(),
        k=3,
        workers=6,
        timeout=5,
        fail_fast_unusable=3,
    )

    assert harness.peak_per_task == 1


@pytest.mark.parametrize("fail_fast", [0, 2])
def test_every_attempt_runs_when_nothing_stops_the_run(tmp_path, fail_fast) -> None:
    """Both schedules produce the same sample when no attempt is unusable."""
    spec_path = tmp_path / "both.eval.yaml"
    spec_path.write_text("tasks: []\n")

    results = run(
        spec=_spec(n_tasks=2),
        spec_path=spec_path,
        harness=BarrierHarness(parties=1),
        judge=PassingJudge(),
        k=3,
        workers=4,
        timeout=5,
        fail_fast_unusable=fail_fast,
    )

    assert [len(t.attempts) for t in results.task_results] == [3, 3]
    assert results.aggregate.avg_score == 1.0
    assert results.run.interrupted is False


class OrderProbe(HarnessBackend):
    """Records the order attempts were started in."""

    def __init__(self) -> None:
        self.started: list[tuple[str, int]] = []

    @property
    def name(self) -> str:
        return "order"

    def run(self, task_id: str, attempt: int, prompt: str, **kwargs) -> AttemptResult:
        self.started.append((task_id, attempt))
        return AttemptResult(
            task_id=task_id,
            attempt=attempt,
            transcript=[],
            final_output="done",
            exit_code=0,
            duration_seconds=0.01,
        )


def test_attempts_are_scheduled_round_robin_across_tasks(tmp_path) -> None:
    """A run that stops early leaves every task sampled, not the first few."""
    spec_path = tmp_path / "rr.eval.yaml"
    spec_path.write_text("tasks: []\n")
    harness = OrderProbe()

    run(
        spec=_spec(n_tasks=3),
        spec_path=spec_path,
        harness=harness,
        judge=PassingJudge(),
        k=2,
        # One worker, so submission order is execution order.
        workers=1,
        timeout=5,
    )

    assert harness.started == [
        ("task-001", 1),
        ("task-002", 1),
        ("task-003", 1),
        ("task-001", 2),
        ("task-002", 2),
        ("task-003", 2),
    ]
