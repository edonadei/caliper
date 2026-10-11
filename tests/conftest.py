"""Shared fakes: the backend and judge doubles, and the CLI-agent boundary.

:class:`ScriptedHarness` and :class:`ScriptedJudge` stand in for a backend and
a judge at the run seam, so a runner test states only the result it is about
(:func:`agent_result`, :func:`failed_result`, :func:`in_turn`).

A real harness starts every process through its :class:`~caliper.harness.spawn.Spawner`.
:func:`patch_cli_calls` gives every harness a :class:`ScriptedSpawner`, which
answers the probes and the agent spawn from one ``fake_run(cmd, **kwargs)``
returning a :class:`subprocess.CompletedProcess`. The fake is called with
``subprocess.run``-shaped keywords (``input``, ``timeout``, ``cwd``, ``env``), so a
test asserts on the arguments it cares about without knowing which call it is
answering.
"""

from __future__ import annotations

import subprocess
import threading
from collections.abc import Callable
from dataclasses import replace

import pytest

from caliper import cancel
from caliper.harness.base import (
    AttemptResult,
    CliHarness,
    ConversationTurn,
    HarnessBackend,
    RunContext,
)
from caliper.harness.spawn import ProcessResult, Spawner
from caliper.judge.base import JudgeResult
from caliper.schema.results import AttemptRecord, Outcome, TaskResult


def agent_result(**overrides) -> AttemptResult:
    """A clean attempt: the agent said "done" and exited 0.

    A test overrides only the fields it is about (``exit_code``,
    ``resolved_model``, ``cancelled``, …).
    """
    fields = dict(
        transcript=[ConversationTurn(role="assistant", content="done")],
        final_output="done",
        exit_code=0,
        duration_seconds=0.1,
    )
    fields.update(overrides)
    return AttemptResult(**fields)


def failed_result(error: str = "agent failed", **overrides) -> AttemptResult:
    """An attempt whose agent exited nonzero with nothing to judge."""
    fields = dict(transcript=[], final_output="", exit_code=1, error=error)
    fields.update(overrides)
    return agent_result(**fields)


def in_turn(*results: AttemptResult) -> Callable[[RunContext], AttemptResult]:
    """A :class:`ScriptedHarness` script: each result in order, then the last again."""
    queue = list(results)
    lock = threading.Lock()

    def next_result(ctx: RunContext) -> AttemptResult:
        with lock:
            return replace(queue.pop(0) if len(queue) > 1 else queue[0])

    return next_result


class ScriptedHarness(HarnessBackend):
    """The backend double: answers every attempt from a script, and records it.

    ``script`` is the :class:`AttemptResult` every invocation returns, or a
    function of the :class:`RunContext` for one that depends on it (the attempt
    number, the workdir, a sequence to step through). ``contexts`` holds what
    each invocation was handed, in the order they arrived.
    """

    def __init__(
        self,
        script: AttemptResult | Callable[[RunContext], AttemptResult] | None = None,
        *,
        name: str = "scripted",
        model: str | None = None,
        supports_mcp: bool = False,
        mcp_unsupported_hint: str | None = None,
    ) -> None:
        self.script = script if script is not None else agent_result()
        self._name = name
        self._model = model
        self.supports_mcp = supports_mcp
        self.mcp_unsupported_hint = mcp_unsupported_hint
        self.contexts: list[RunContext] = []
        # Attempts run on worker threads (docs/adr/0018).
        self._lock = threading.Lock()

    @property
    def name(self) -> str:
        return self._name

    @property
    def calls(self) -> int:
        return len(self.contexts)

    @property
    def attempts(self) -> list[int]:
        return [ctx.attempt for ctx in self.contexts]

    def run(self, ctx: RunContext) -> AttemptResult:
        with self._lock:
            self.contexts.append(ctx)
        if callable(self.script):
            return self.script(ctx)
        # A shallow copy: each attempt gets its own result object, though the
        # transcript's turns are shared (nothing downstream mutates them).
        return replace(self.script)


class ScriptedJudge:
    """The judge double: answers every attempt with one verdict, and counts calls."""

    backend = "test"
    model = None
    prompt_version = None

    def __init__(self, result: JudgeResult | None = None) -> None:
        self.result = result or JudgeResult(passed=True)
        self.calls = 0
        self._lock = threading.Lock()

    def evaluate(self, task, transcript, workdir) -> JudgeResult:
        with self._lock:
            self.calls += 1
        return self.result


def task_result(
    *outcomes: Outcome,
    task_id: str = "task-001",
    name: str = "One",
    expected: list[str] | None = None,
) -> TaskResult:
    """A task result built from nothing but its attempt outcomes.

    Which is all a task result needs: it derives every count and metric from the
    attempts it is handed, so a test states the outcomes and nothing else.
    """
    return TaskResult(
        task_id=task_id,
        task_name=name,
        attempts=[
            AttemptRecord(attempt=i, output="", duration_seconds=0.0, outcome=outcome)
            for i, outcome in enumerate(outcomes, start=1)
        ],
        activation_expected=expected,
    )


def run_context(**overrides) -> RunContext:
    """A ``RunContext`` with everything a hook needs already filled in.

    Shared so a test that cares about one field (the isolated home, the extra
    path) says only that field, and so the conformance suite can hand the same
    context to all four adapters.
    """
    fields = {
        "task_id": "task-001",
        "attempt": 1,
        "prompt": "Hello",
        "skill_refs": [],
        "model": None,
        "timeout": 12,
        "isolated_home": "/tmp/caliper-test-home",
        "workdir": "/tmp/caliper-test-home/work",
        "extra_path": [],
    }
    return RunContext(**{**fields, **overrides})


class ScriptedSpawner(Spawner):
    """A :class:`Spawner` whose every process is answered by ``fake_run``.

    ``fake_run(cmd, **kwargs)`` gets ``subprocess.run``-shaped keywords
    (``input``, ``timeout``, ``cwd``, ``env``, and ``stdin`` as ``DEVNULL`` when
    the agent is given none) and returns a :class:`subprocess.CompletedProcess`.
    """

    def __init__(self, fake_run: Callable[..., subprocess.CompletedProcess]) -> None:
        self._fake_run = fake_run

    def _run(self, cmd, *, timeout):
        return self._fake_run(cmd, capture_output=True, text=True, timeout=timeout)

    def execute(self, cmd, *, env, cwd, timeout, stdin, label):
        kwargs = (
            {"input": stdin} if stdin is not None else {"stdin": subprocess.DEVNULL}
        )
        try:
            completed = self._fake_run(
                cmd,
                **kwargs,
                env=env,
                cwd=cwd,
                timeout=timeout,
                capture_output=True,
                text=True,
            )
        except OSError as exc:
            return ProcessResult("", f"{label} CLI failed: {exc}", 1, False)
        return ProcessResult(
            stdout=(completed.stdout or "").strip(),
            stderr=(completed.stderr or "").strip(),
            returncode=completed.returncode,
            timed_out=False,
        )


def patch_cli_calls(
    monkeypatch, fake_run: Callable[..., subprocess.CompletedProcess]
) -> None:
    """Answer every process a CLI harness starts from ``fake_run``."""
    monkeypatch.setattr(CliHarness, "spawner", ScriptedSpawner(fake_run))


@pytest.fixture(autouse=True)
def _clear_cancellation():
    """No test leaks a cancelled run into the next one.

    ``caliper.cancel`` is process-global (see its module docstring), and a test
    that cancels a run would otherwise leave every later spawn killed on sight.
    """
    yield
    cancel.reset()


@pytest.fixture
def attempt_workdir(tmp_path):
    """A live attempt workdir whose spec dir is ``tmp_path``."""
    from caliper.workdir import AttemptWorkdir

    with AttemptWorkdir(tmp_path) as workdir:
        yield workdir
