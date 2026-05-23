from __future__ import annotations

from caliper.reporter import make_progress


def test_make_progress_initializes_task_totals() -> None:
    progress, task_ids = make_progress(["Task one", "Task two"], k=3)

    assert progress.tasks[task_ids["Task one"]].total == 3
    assert progress.tasks[task_ids["Task two"]].total == 3
