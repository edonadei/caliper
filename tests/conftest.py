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


class _FakePopen:
    """The bits of ``Popen`` that ``CliHarness._execute`` touches.

    ``fake_run`` is deliberately not called until :meth:`communicate`: ``input``
    and ``timeout`` arrive there rather than at construction, and a fake that
    asserts on them has to see the same call shape ``subprocess.run`` would have
    delivered. A ``TimeoutExpired`` raised by the fake propagates untouched —
    that is exactly the signal ``_execute`` turns into a 124/timeout result.
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
