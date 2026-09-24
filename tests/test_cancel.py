"""Ctrl-C stops a run without throwing away what it already paid for.

The behaviours under test: attempts already finished are kept and saved,
attempts the cancellation killed are dropped rather than recorded as
infrastructure failures, an agent in flight is actually killed (not waited out),
and a fatal error diagnosed mid-run salvages the same way an interrupt does.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Iterator

import pytest
import psutil
from typer.testing import CliRunner

from caliper import cancel
from caliper.harness.base import (
    AttemptResult,
    CliHarness,
    ConversationTurn,
    HarnessBackend,
    HarnessConfigurationError,
    ProcessResult,
    RunContext,
)
from caliper.judge.base import JudgeResult
from caliper.main import app
from caliper.runner import RunAborted, run
from caliper.schema.results import (
    AggregateScore,
    AttemptRecord,
    Outcome,
    RunMeta,
    RunResults,
    TaskResult,
)
from caliper.schema.spec import EvalSpec, TaskSpec

runner = CliRunner()


class PassingJudge:
    backend = "test"
    model = None

    def evaluate(
        self, task, transcript, final_output, spec_dir, workdir
    ) -> JudgeResult:
        return JudgeResult(passed=True, reasoning="ok")


class CancellingHarness(HarnessBackend):
    """Finishes one attempt, then asks the run to stop — like a Ctrl-C mid-run."""

    def __init__(self, cancel_after: int = 1, then_fail: bool = False) -> None:
        self.cancel_after = cancel_after
        self.then_fail = then_fail
        self.started: list[int] = []

    @property
    def name(self) -> str:
        return "cancelling"

    def run(self, ctx: RunContext) -> AttemptResult:
        self.started.append(ctx.attempt)
        if ctx.attempt >= self.cancel_after:
            cancel.request()
        if self.then_fail:
            # What a killed agent looks like coming back: a non-zero exit,
            # nothing to show for it, and — the part only the spawn knows —
            # `cancelled`, saying *we* killed it rather than it failing.
            return AttemptResult(
                transcript=[],
                final_output="",
                exit_code=-9,
                duration_seconds=0.01,
                error="killed",
                cancelled=True,
            )
        return AttemptResult(
            transcript=[ConversationTurn(role="assistant", content="done")],
            final_output="done",
            exit_code=0,
            duration_seconds=0.01,
        )


class ExpiringHarness(HarnessBackend):
    """Runs, then starts failing the way an expired credential does."""

    def __init__(self, fail_from: int = 2) -> None:
        self.fail_from = fail_from

    @property
    def name(self) -> str:
        return "expiring"

    def run(self, ctx: RunContext) -> AttemptResult:
        if ctx.attempt >= self.fail_from:
            raise HarnessConfigurationError("credentials expired mid-run")
        return AttemptResult(
            transcript=[],
            final_output="done",
            exit_code=0,
            duration_seconds=0.01,
        )


class SleepHarness(CliHarness):
    """A CLI harness whose agent is ``sleep`` — something to interrupt."""

    @property
    def name(self) -> str:
        return "sleep"

    def skills_root(self, ctx) -> Path:  # pragma: no cover - never installs
        return Path(ctx.isolated_home) / "skills"

    def _command(self, ctx):  # pragma: no cover - driven through _execute
        return ["sleep", "30"], None, None

    def _environment(self, ctx) -> dict[str, str]:  # pragma: no cover
        return {}

    def _parse_stream(self, stdout: str):
        return [], stdout


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


def _spec_file(tmp_path: Path) -> Path:
    path = tmp_path / "sample.eval.yaml"
    path.write_text("tasks: []\n")
    return path


def test_cancelling_keeps_the_attempts_already_paid_for(tmp_path) -> None:
    harness = CancellingHarness(cancel_after=1)

    results = run(
        spec=_spec(),
        spec_path=_spec_file(tmp_path),
        harness=harness,
        judge=PassingJudge(),
        k=5,
        workers=1,
        timeout=5,
    )

    attempts = results.task_results[0].attempts
    assert [a.attempt for a in attempts] == [1]
    assert attempts[0].outcome == Outcome.PASS
    assert results.run.interrupted is True
    # Scored over what ran, not over the k that was asked for.
    assert results.task_results[0].score == 1.0


def test_a_cancelled_run_does_not_start_the_remaining_attempts(tmp_path) -> None:
    harness = CancellingHarness(cancel_after=1)

    run(
        spec=_spec(n_tasks=2),
        spec_path=_spec_file(tmp_path),
        harness=harness,
        judge=PassingJudge(),
        k=4,
        workers=1,
        timeout=5,
    )

    # One attempt ran and cancelled the run; nothing after it was started.
    assert harness.started == [1]


class FailsOnItsOwnHarness(HarnessBackend):
    """Fails for real, and cancels the run from underneath itself.

    The awkward case: an attempt that was dying of its own causes while the
    interrupt landed. It was never killed, so its failure is a real observation.
    """

    @property
    def name(self) -> str:
        return "failing"

    def run(self, ctx: RunContext) -> AttemptResult:
        cancel.request()
        return AttemptResult(
            transcript=[],
            final_output="",
            exit_code=1,
            duration_seconds=0.01,
            error="Error 503: service unavailable",
            # Not cancelled: nothing killed this, it fell over on its own.
        )


def test_attempts_killed_by_the_cancellation_are_not_recorded(tmp_path) -> None:
    """An interrupt must not show up in the sample as an infrastructure failure."""
    # Attempt 1 is killed by the very cancellation it triggers — the shape of a
    # SIGKILL landing on an agent that was mid-flight.
    harness = CancellingHarness(cancel_after=1, then_fail=True)

    results = run(
        spec=_spec(),
        spec_path=_spec_file(tmp_path),
        harness=harness,
        judge=PassingJudge(),
        k=3,
        workers=1,
        timeout=5,
    )

    assert harness.started == [1]
    assert results.task_results[0].attempts == []
    assert results.run.interrupted is True


def test_an_attempt_that_failed_on_its_own_is_kept(tmp_path) -> None:
    """Dropping on the outcome would delete the evidence you interrupted.

    Interrupt a run during a real throttling storm and the dead attempts are of
    two kinds: the ones the storm killed (observations) and the ones the
    interrupt killed (artefacts). Only the spawn knows which is which.
    """
    results = run(
        spec=_spec(),
        spec_path=_spec_file(tmp_path),
        harness=FailsOnItsOwnHarness(),
        judge=PassingJudge(),
        k=3,
        workers=1,
        timeout=5,
    )

    attempts = results.task_results[0].attempts
    assert [a.outcome for a in attempts] == [Outcome.INFRA_ERROR]
    assert results.task_results[0].unusable == 1
    assert results.run.interrupted is True


def test_a_fatal_error_mid_run_salvages_the_attempts_that_ran(tmp_path) -> None:
    with pytest.raises(RunAborted) as excinfo:
        run(
            spec=_spec(),
            spec_path=_spec_file(tmp_path),
            harness=ExpiringHarness(fail_from=2),
            judge=PassingJudge(),
            k=4,
            workers=1,
            timeout=5,
        )

    aborted = excinfo.value
    assert isinstance(aborted.cause, HarnessConfigurationError)
    assert "credentials expired" in str(aborted.cause)
    assert [a.attempt for a in aborted.results.task_results[0].attempts] == [1]
    assert aborted.results.run.interrupted is True


class UnavailableModelJudge:
    """A judge whose model the provider does not know, as #139 reports it."""

    backend = "claude-code"
    model = "claude-bogus"

    def evaluate(
        self, task, transcript, final_output, spec_dir, workdir
    ) -> JudgeResult:
        raise HarnessConfigurationError("Judge model 'claude-bogus' is unavailable")


def test_an_unavailable_judge_model_stops_the_run_at_the_first_attempt(
    tmp_path,
) -> None:
    """No saved run is made up entirely of judge errors (issue #139)."""
    harness = CancellingHarness(cancel_after=99)

    with pytest.raises(RunAborted) as excinfo:
        run(
            spec=_spec(),
            spec_path=_spec_file(tmp_path),
            harness=harness,
            judge=UnavailableModelJudge(),
            k=4,
            workers=1,
            timeout=5,
        )

    aborted = excinfo.value
    assert "claude-bogus" in str(aborted.cause)
    assert harness.started == [1]
    assert aborted.results.task_results[0].attempts == []


def test_cancel_kills_an_agent_in_flight(tmp_path) -> None:
    """The point of the whole mechanism: not waiting out the timeout."""
    cancel.reset()
    harness = SleepHarness()
    finished = threading.Event()
    box: dict[str, object] = {}

    def spawn() -> None:
        box["result"] = harness._execute(
            ["sleep", "30"],
            env=dict(os.environ),
            cwd=str(tmp_path),
            timeout=30,
            stdin=None,
        )
        finished.set()

    thread = threading.Thread(target=spawn)
    started = time.monotonic()
    thread.start()
    # A cancellation that lands before the spawn is covered too — `track` kills
    # the process at registration when the flag is already set.
    time.sleep(0.2)
    cancel.request()

    assert finished.wait(timeout=10), "the agent was not killed"
    thread.join()
    assert time.monotonic() - started < 10
    assert box["result"].returncode != 0


def test_killed_process_does_not_match_reused_pid() -> None:
    cancel.reset()
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        start_new_session=True,
    )
    try:
        cancel.kill(proc)
        proc.wait(timeout=5)
        assert cancel.was_killed(proc)
        assert not cancel.was_killed(SimpleNamespace(pid=proc.pid))
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def _pid_alive(pid: int) -> bool:
    try:
        return psutil.Process(pid).status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False


@contextmanager
def _running_attempt(
    tmp_path: Path, command: list[str], pid_file: Path, timeout: int
) -> Iterator[tuple[list[int], threading.Event, dict[str, ProcessResult]]]:
    cancel.reset()
    finished = threading.Event()
    box: dict[str, ProcessResult] = {}

    def execute() -> None:
        box["result"] = SleepHarness()._execute(
            command,
            env=dict(os.environ),
            cwd=str(tmp_path),
            timeout=timeout,
            stdin=None,
        )
        finished.set()

    thread = threading.Thread(target=execute)
    thread.start()
    pids: list[int] = []
    try:
        deadline = time.monotonic() + 5
        while not pid_file.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert pid_file.exists(), "the agent did not start its tools"
        pids = [int(pid) for pid in pid_file.read_text().split()]
        yield pids, finished, box
    finally:
        cancel.request()
        for pid in pids:
            try:
                psutil.Process(pid).kill()
            except psutil.NoSuchProcess:
                pass
        thread.join(timeout=5)
        cancel.reset()


@pytest.mark.parametrize("stop", ["cancel", "timeout"])
def test_stopping_an_attempt_kills_tools_in_their_own_process_groups(
    tmp_path: Path, stop: str
) -> None:
    """Codex-style tools start new sessions and can spawn tools of their own."""
    pid_file = tmp_path / "tool-pids"
    tool = (
        "import os, pathlib, subprocess, sys; "
        "leaf = subprocess.Popen([sys.executable, '-c', "
        "'import time; time.sleep(30)'], start_new_session=True, "
        "stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL); "
        "pathlib.Path(sys.argv[1]).write_text(f'{os.getpid()} {leaf.pid}'); "
        "leaf.wait()"
    )
    agent = (
        "import subprocess, sys; "
        "tool = subprocess.Popen([sys.executable, '-c', sys.argv[2], sys.argv[1]], "
        "start_new_session=True, stdout=subprocess.DEVNULL, "
        "stderr=subprocess.DEVNULL); tool.wait()"
    )
    with _running_attempt(
        tmp_path,
        [sys.executable, "-c", agent, str(pid_file), tool],
        pid_file,
        1 if stop == "timeout" else 30,
    ) as (pids, finished, box):
        assert all(_pid_alive(pid) for pid in pids)
        if stop == "cancel":
            cancel.request()
        assert finished.wait(5), "the attempt did not stop promptly"
        assert box["result"].timed_out is (stop == "timeout")
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and any(_pid_alive(pid) for pid in pids):
            time.sleep(0.01)
        assert not any(_pid_alive(pid) for pid in pids), (
            "an agent tool survived the attempt"
        )


@pytest.mark.parametrize("stop", ["cancel", "timeout"])
def test_stopping_an_attempt_reaches_a_tool_after_its_agent_exits(
    tmp_path: Path, stop: str
) -> None:
    """A detached tool can spawn another tool after the CLI has exited."""
    pid_file = tmp_path / "pids"
    trigger = tmp_path / "spawn-leaf"
    leaf_pid_file = tmp_path / "leaf-pid"
    tool = (
        "import pathlib, subprocess, sys, time\n"
        "while not pathlib.Path(sys.argv[1]).exists():\n"
        "    time.sleep(0.005)\n"
        "leaf = subprocess.Popen([sys.executable, '-c', "
        "'import time; time.sleep(10)'], start_new_session=True)\n"
        "pathlib.Path(sys.argv[2]).write_text(str(leaf.pid))\n"
        "leaf.wait()\n"
    )
    agent = (
        "import os, pathlib, subprocess, sys; "
        "tool = subprocess.Popen([sys.executable, '-c', sys.argv[2], "
        "sys.argv[3], sys.argv[4]], start_new_session=True); "
        "pathlib.Path(sys.argv[1]).write_text(f'{os.getpid()} {tool.pid}')"
    )
    with _running_attempt(
        tmp_path,
        [
            sys.executable,
            "-c",
            agent,
            str(pid_file),
            tool,
            str(trigger),
            str(leaf_pid_file),
        ],
        pid_file,
        2 if stop == "timeout" else 30,
    ) as (pids, finished, box):
        deadline = time.monotonic() + 5
        agent_pid, tool_pid = pids
        while _pid_alive(agent_pid) and time.monotonic() < deadline:
            time.sleep(0.01)
        assert not _pid_alive(agent_pid)
        assert _pid_alive(tool_pid)
        assert not leaf_pid_file.exists()
        trigger.touch()
        while not leaf_pid_file.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert leaf_pid_file.exists(), "the detached tool did not spawn its child"
        leaf_pid = int(leaf_pid_file.read_text())
        pids.append(leaf_pid)
        assert _pid_alive(leaf_pid)
        if stop == "cancel":
            cancel.request()
        assert finished.wait(5), "the orphaned tool held the attempt open"
        assert box["result"].timed_out is (stop == "timeout")
        deadline = time.monotonic() + 5
        while any(_pid_alive(pid) for pid in pids) and time.monotonic() < deadline:
            time.sleep(0.01)
        assert not any(_pid_alive(pid) for pid in pids), (
            "a detached tool survived its agent"
        )


@pytest.mark.parametrize("stop", ["cancel", "timeout"])
def test_stopping_does_not_hang_on_an_unidentifiable_pipe_holder(
    tmp_path: Path, monkeypatch, stop: str
) -> None:
    """Even a tool that clears its tag cannot hold the run open forever."""
    threads_before = set(threading.enumerate())
    monkeypatch.setattr(psutil.Process, "children", lambda self, recursive=False: [])
    monkeypatch.setattr(cancel, "_tagged_processes", lambda tag: set())
    pid_file = tmp_path / "pids"
    agent = (
        "import os, pathlib, subprocess, sys; "
        "tool = subprocess.Popen([sys.executable, '-c', "
        "'import time; time.sleep(30)'], env={}, start_new_session=True, "
        "stdout=sys.stdout, stderr=sys.stderr); "
        "pathlib.Path(sys.argv[1]).write_text(f'{os.getpid()} {tool.pid}'); "
        "os._exit(0)"
    )

    with _running_attempt(
        tmp_path,
        [sys.executable, "-c", agent, str(pid_file)],
        pid_file,
        1 if stop == "timeout" else 5,
    ) as (pids, finished, box):
        assert _pid_alive(pids[1])
        if stop == "cancel":
            cancel.request()
        assert finished.wait(4), "the inherited pipe held the attempt open"
        assert box["result"].timed_out is (stop == "timeout")
        assert box["result"].cancelled is (stop == "cancel")
        # Only the fixture's execute thread may still be exiting. A blocked
        # communication worker would remain here while the tool is alive.
        assert len(set(threading.enumerate()) - threads_before) <= 1


def test_large_prompt_reaches_agent_after_a_slow_stdin_start(tmp_path: Path) -> None:
    prompt = "x" * 1_000_000
    result = SleepHarness()._execute(
        [
            sys.executable,
            "-c",
            "import sys,time; time.sleep(0.3); print(len(sys.stdin.read()))",
        ],
        env=dict(os.environ),
        cwd=str(tmp_path),
        timeout=5,
        stdin=prompt,
    )

    assert result.returncode == 0
    assert result.stdout == str(len(prompt))


@pytest.mark.skipif(os.name != "posix", reason="chmod does not lock Windows dirs")
def test_prompt_stdin_works_from_read_only_workdir(tmp_path: Path) -> None:
    tmp_path.chmod(0o555)
    try:
        result = SleepHarness()._execute(
            [sys.executable, "-c", "import sys; print(sys.stdin.read())"],
            env=dict(os.environ),
            cwd=str(tmp_path),
            timeout=5,
            stdin="prompt",
        )
    finally:
        tmp_path.chmod(0o755)

    assert result.returncode == 0
    assert result.stdout == "prompt"


def _one_attempt_run(k: int = 3) -> RunResults:
    """A partial run with something in it — the shape salvage exists to keep."""
    return RunResults(
        run=RunMeta(
            spec="sample",
            timestamp=datetime(2026, 7, 3, tzinfo=timezone.utc),
            k=k,
            backend="claude-code",
            interrupted=True,
        ),
        skill_snapshots=[],
        task_results=[
            TaskResult(
                task_id="task-001",
                task_name="One",
                attempts=[
                    AttemptRecord(
                        attempt=1,
                        output="ok",
                        duration_seconds=1.0,
                        outcome=Outcome.PASS,
                    )
                ],
            )
        ],
        aggregate=AggregateScore(avg_score=1.0, per_task=[]),
    )


def test_run_cli_exits_130_and_saves_an_interrupted_run(monkeypatch, tmp_path) -> None:
    # `run` roots its store at the cwd (docs/adr/0022), so the run this asserts
    # on lands under tmp_path only if that is where caliper is invoked from.
    monkeypatch.chdir(tmp_path)
    spec_file = tmp_path / "sample.eval.yaml"
    spec_file.write_text(
        "tasks:\n  - name: One\n    prompt: Do it\n    assert: 'assert True'\n"
    )

    def fake_run(**kwargs):
        return _one_attempt_run(k=kwargs["k"])

    monkeypatch.setattr("caliper.commands.run.get_harness", lambda *a, **kw: object())
    monkeypatch.setattr("caliper.commands.run.EvalJudge", lambda *a, **kw: object())
    monkeypatch.setattr("caliper.commands.run.run", fake_run)

    result = runner.invoke(app, ["run", str(spec_file)])

    assert result.exit_code == 130
    assert "interrupted" in result.stdout
    saved = list((tmp_path / ".caliper" / "results" / "sample").glob("*.json"))
    assert len(saved) == 1


def test_run_cli_saves_before_reporting_a_fatal_error(monkeypatch, tmp_path) -> None:
    # `run` roots its store at the cwd (docs/adr/0022), so the run this asserts
    # on lands under tmp_path only if that is where caliper is invoked from.
    monkeypatch.chdir(tmp_path)
    spec_file = tmp_path / "sample.eval.yaml"
    spec_file.write_text(
        "tasks:\n  - name: One\n    prompt: Do it\n    assert: 'assert True'\n"
    )

    partial = _one_attempt_run()

    def fake_run(**kwargs):
        raise RunAborted(HarnessConfigurationError("credentials expired"), partial)

    monkeypatch.setattr("caliper.commands.run.get_harness", lambda *a, **kw: object())
    monkeypatch.setattr("caliper.commands.run.EvalJudge", lambda *a, **kw: object())
    monkeypatch.setattr("caliper.commands.run.run", fake_run)

    result = runner.invoke(app, ["run", str(spec_file)])

    assert result.exit_code == 2
    assert "credentials expired" in result.stdout
    saved = list((tmp_path / ".caliper" / "results" / "sample").glob("*.json"))
    assert len(saved) == 1
