"""Cooperative cancellation for a run already in flight.

Ctrl-C during a run has to do two things Python's default handler cannot do on
its own: kill the agents that are **already** spawned — each can hold a worker
for the whole ``--timeout`` — and let the runner *return* the attempts it has
already paid for. Without both, an interrupt unwinds through
``ThreadPoolExecutor.__exit__``, which waits for every in-flight attempt before
the exception is even raised, and then discards a run that may have cost
minutes and real money.

State is process-global on purpose: the signal it answers is process-global, and
a token threaded through :class:`~caliper.harness.base.HarnessBackend` would
have to cross every backend's ``run`` to reach the one place that can kill a
subprocess — widening the narrow seam for a fact none of those backends vary.
:func:`reset` is called at the top of each run, so this is a run's scratch state
rather than a lifetime singleton.
"""

from __future__ import annotations

import os
import signal
import subprocess
import threading
from contextlib import contextmanager
from typing import Iterator

_lock = threading.Lock()
_live: set[subprocess.Popen] = set()
_requested = threading.Event()


def reset() -> None:
    """Clear cancellation state. Called at the start of every run."""
    _requested.clear()
    with _lock:
        _live.clear()


def request() -> None:
    """Stop the run: refuse further attempts and kill the ones in flight.

    Safe to call from a signal handler and from a worker thread (a fatal
    misconfiguration diagnosed mid-run cancels the same way an interrupt does).
    """
    _requested.set()
    with _lock:
        live = list(_live)
    for proc in live:
        kill(proc)


def requested() -> bool:
    """Whether the run has been asked to stop."""
    return _requested.is_set()


@contextmanager
def track(proc: subprocess.Popen) -> Iterator[subprocess.Popen]:
    """Register a spawned agent so :func:`request` can reach it.

    A cancellation that lands between the spawn and the registration would
    otherwise leave that one process running for its full timeout, so the
    flag is re-checked once inside.
    """
    with _lock:
        _live.add(proc)
    try:
        if requested():
            kill(proc)
        yield proc
    finally:
        with _lock:
            _live.discard(proc)


def kill(proc: subprocess.Popen) -> None:
    """Kill an agent and everything it spawned.

    The whole process group, not just the child: an agent CLI spawns tools of
    its own, and killing only the parent orphans them holding the isolated home
    open. Backends spawn with ``start_new_session=True`` precisely so there is a
    group to address here. ``SIGKILL`` rather than a graceful term — the
    attempt's output is discarded either way, so there is nothing to flush.
    """
    if proc.poll() is not None:
        return
    try:
        if hasattr(os, "killpg"):
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        else:  # pragma: no cover - posix-only project, kept honest anyway
            proc.kill()
    except (OSError, ProcessLookupError):
        pass
