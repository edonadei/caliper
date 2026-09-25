"""Shared fakes for the CLI-agent boundary.

A harness reaches the outside world twice, and a test that fakes a CLI wants to
answer both from one function:

* short capability probes (``codex --version``) still go through
  ``subprocess.run``;
* the agent spawn goes through ``subprocess.Popen``, so a run can kill what is
  in flight when it is cancelled (see :mod:`caliper.cancel`).

:func:`patch_cli_calls` routes both at one ``fake_run(cmd, **kwargs)`` that
returns a :class:`subprocess.CompletedProcess`, adapting it to the slice of the
Popen protocol ``CliHarness._execute`` uses. The fake is called with
``subprocess.run``-shaped keywords (``input``, ``timeout``, ``cwd``, ``env``),
not Popen's, so a test can keep asserting on the arguments it cares about
without knowing which of the two calls it is answering.
"""

from __future__ import annotations

import subprocess
from typing import Callable

import pytest

from caliper import cancel
from caliper.harness.base import RunContext
from caliper.schema.results import AttemptRecord, Outcome, TaskResult


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


class StubHarness:
    """A harness double for CLI tests whose ``run`` is itself stubbed.

    ``caliper run`` reads ``supports_mcp`` to decide the start-of-run notice
    (docs/adr/0028).
    """

    def __init__(self, supports_mcp: bool = False) -> None:
        self.supports_mcp = supports_mcp


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


class _FakePopen:
    """The bits of ``Popen`` that ``CliHarness._execute`` touches.

    ``fake_run`` is deliberately not called until :meth:`communicate`. For a
    file-backed stdin, read the payload the real child would have consumed so
    existing prompt assertions can inspect it. A ``TimeoutExpired`` raised by
    the fake propagates untouched to the harness timeout handler.
    """

    def __init__(
        self, fake_run: Callable[..., subprocess.CompletedProcess], cmd, kwargs: dict
    ) -> None:
        self._fake_run = fake_run
        self._cmd = cmd
        # Popen-only plumbing that has no ``subprocess.run`` counterpart.
        # ``stdin`` is kept: run takes it too, and a backend's choice between
        # DEVNULL and a payload is a fact tests assert on.
        self._kwargs = {
            k: v
            for k, v in kwargs.items()
            if k not in ("stdout", "stderr", "start_new_session")
        }
        self._completed: subprocess.CompletedProcess | None = None
        self.pid = 4242

    def __enter__(self) -> "_FakePopen":
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None

    def communicate(self, input=None, timeout=None):  # noqa: A002 - Popen's name
        source = self._kwargs.get("stdin")
        if input is None and hasattr(source, "read"):
            source.seek(0)
            input = source.read().decode("utf-8")
        kwargs = dict(self._kwargs, input=input, timeout=timeout)
        # What ``subprocess.run`` would have been given for the same spawn: the
        # two pipes it sets itself, and whatever text handling the caller asked
        # for (already in the Popen kwargs, hence setdefault).
        kwargs.setdefault("capture_output", True)
        kwargs.setdefault("text", True)
        self._completed = self._fake_run(self._cmd, **kwargs)
        return self._completed.stdout, self._completed.stderr

    def poll(self) -> int | None:
        return None if self._completed is None else self._completed.returncode

    @property
    def returncode(self) -> int | None:
        return self.poll()

    def kill(self) -> None:
        return None


def patch_cli_calls(
    monkeypatch, fake_run: Callable[..., subprocess.CompletedProcess]
) -> None:
    """Answer both the probe (``subprocess.run``) and the spawn (``Popen``)."""
    monkeypatch.setattr("caliper.harness.base.subprocess.run", fake_run)
    monkeypatch.setattr(
        "caliper.harness.base.subprocess.Popen",
        lambda cmd, **kwargs: _FakePopen(fake_run, cmd, kwargs),
    )


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
