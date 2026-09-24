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
import weakref
from contextlib import contextmanager
from typing import Iterator

import psutil

_lock = threading.Lock()
_live: set[subprocess.Popen] = set()
_killed: weakref.WeakSet[subprocess.Popen] = weakref.WeakSet()
_descendants: dict[subprocess.Popen, set[psutil.Process]] = {}
_tags: dict[subprocess.Popen, str] = {}
_requested = threading.Event()

# Inherited by agent tools, including tools that start new sessions or are
# reparented before a timeout. A fresh value is assigned to every CLI call.
PROCESS_TAG = "CALIPER_PROCESS_TAG"


def reset() -> None:
    """Clear cancellation state. Called at the start of every run."""
    _requested.clear()
    with _lock:
        _live.clear()
        _killed.clear()
        _descendants.clear()
        _tags.clear()


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


def sleep_unless_stopped(seconds: float) -> bool:
    """Sleep, unless the run stops first. Returns **True when it stopped**.

    Named for what the return value means rather than for the wait, because the
    call site reads as a question: ``if cancel.sleep_unless_stopped(delay):``.

    A retry backoff has to be interruptible, or Ctrl-C during one would sit out
    the whole delay for no reason — the attempt it is waiting to retry is
    already never going to run.
    """
    return _requested.wait(timeout=seconds)


@contextmanager
def track(
    proc: subprocess.Popen,
    *,
    cancel_if_requested: bool = True,
    process_tag: str | None = None,
) -> Iterator[subprocess.Popen]:
    """Register a spawned process so :func:`request` can reach it.

    A cancellation that lands between the spawn and the registration would
    otherwise leave that one process running for its full timeout, so the
    flag is re-checked once inside. Cleanup hooks can skip that immediate kill
    so they still run after an interrupt; a new cancellation during cleanup
    still reaches the registered process.
    """
    known: set[psutil.Process] = set()
    stopped = threading.Event()

    def watch_tree() -> None:
        # A CLI can exit while a detached tool still holds its output pipe.
        # Keep process identities before that child is reparented, so a later
        # timeout can still reach it after the CLI itself has gone away.
        while not stopped.is_set():
            try:
                children = psutil.Process(proc.pid).children(recursive=True)
            except psutil.Error:
                return
            with _lock:
                known.update(children)
            if proc.poll() is not None:
                return
            stopped.wait(0.02)

    with _lock:
        _live.add(proc)
        _descendants[proc] = known
        if process_tag is not None:
            _tags[proc] = process_tag
    watcher = threading.Thread(
        target=watch_tree, name="caliper-process-tree", daemon=True
    )
    watcher.start()
    try:
        if cancel_if_requested and requested():
            kill(proc)
        yield proc
    finally:
        stopped.set()
        watcher.join()
        with _lock:
            _live.discard(proc)
            _descendants.pop(proc, None)
            _tags.pop(proc, None)


def was_killed(proc: subprocess.Popen) -> bool:
    """Whether *we* killed this process, rather than it failing on its own.

    The difference decides whether an attempt is evidence. A run that is
    cancelled mid-throttling-storm has two kinds of dead attempt in it — the
    ones the storm killed, which are real observations, and the ones this module
    killed, which are artefacts of the interrupt — and an outcome alone cannot
    tell them apart, since both come back as a non-zero exit.
    """
    with _lock:
        return proc in _killed


def kill(proc: subprocess.Popen) -> None:
    """Kill an agent and everything it spawned.

    Snapshot descendants before killing the parent: tools may start their own
    process groups, and after the parent dies they are reparented and can no
    longer be found by walking its tree. Also kill the agent's own group to
    catch members of its session. ``SIGKILL`` ends a cancelled or timed-out
    attempt promptly; the caller can still retain output it already captured.
    """
    with _lock:
        descendants = set(_descendants.get(proc, ()))
        tag = _tags.get(proc)
    try:
        descendants.update(psutil.Process(proc.pid).children(recursive=True))
    except psutil.Error:
        pass
    if tag is not None:
        descendants.update(
            child for child in _tagged_processes(tag) if child.pid != proc.pid
        )
    # The watcher remembers every child it saw, including ones that already
    # exited. Only live ones still need killing, and only a CLI that was still
    # running counts as killed: a finished attempt stays a real observation.
    descendants = {child for child in descendants if child.is_running()}
    finished = proc.poll() is not None
    if finished and not descendants:
        return
    if not finished:
        with _lock:
            _killed.add(proc)
    # The stored psutil.Process handles retain identity across reparenting and
    # guard against killing an unrelated process if the OS recycles a PID.
    for child in descendants:
        try:
            child.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    try:
        if hasattr(os, "killpg"):
            os.killpg(proc.pid, signal.SIGKILL)
        else:  # pragma: no cover - posix-only project, kept honest anyway
            proc.kill()
    except (OSError, ProcessLookupError):
        pass
    psutil.wait_procs(list(descendants), timeout=1)
    # A tool can fork after the first snapshot but before it receives SIGKILL.
    # Scan again once its known parents have stopped.
    if tag is not None:
        for child in _tagged_processes(tag):
            if child.pid == proc.pid:
                continue
            try:
                child.kill()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass


def _tagged_processes(tag: str) -> set[psutil.Process]:
    """Find this invocation's descendants even after their parent has exited."""
    matches: set[psutil.Process] = set()
    for child in psutil.process_iter():
        try:
            if child.environ().get(PROCESS_TAG) == tag:
                matches.add(child)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return matches
