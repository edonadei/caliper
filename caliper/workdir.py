"""The attempt workdir: where the agent, its hooks and its assertions all run.

One fresh directory per attempt, shared by ``setup:``, the agent, ``assert:``
and ``cleanup:``, so a relative path means the same place to every step. See
docs/adr/0026-an-attempt-runs-in-one-fresh-workdir.md.

:class:`AttemptWorkdir` owns the attempt's temp dir — the workdir and, beside
it, the agent's isolated home — and runs every **step** (docs/CONTEXT.md →
Step) there: the spec author's code, as opposed to the agent under test.
"""

from __future__ import annotations

import os
import select
import shutil
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from caliper import cancel

#: The attempt workdir, for a step that has changed directory.
WORKDIR_ENV = "CALIPER_WORKDIR"
#: The spec's own directory, where an author keeps fixtures to copy in.
SPEC_DIR_ENV = "CALIPER_SPEC_DIR"

StepPhase = Literal["setup", "assert", "check", "cleanup"]

# Enough of a step's output to diagnose it, bounded so a noisy step cannot hold
# its whole output in memory. Each reader cuts it to the size it records.
_OUTPUT_TAIL = 16000


class StepCancelled(Exception):
    """A step was killed by the run's cancellation, so it observed nothing.

    Raised rather than returned: a cancelled step is not a result, and nothing
    between the step and the attempt has anything to do with it but pass it on.
    """


@dataclass(frozen=True)
class StepResult:
    exit_code: int
    # The tail of what the step printed, stripped.
    output: str
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out


class AttemptWorkdir:
    """One attempt's temp dir, and the steps run in its workdir.

    Use as a context manager: entering creates the temp dir with an empty
    ``work/`` in it; leaving removes both, and the isolated home with them.
    """

    def __init__(self, spec_dir: str | Path) -> None:
        # Absolute, because every step runs in the workdir: a relative spec dir
        # would name a path under it.
        self.spec_dir = str(Path(spec_dir).resolve())
        self._root: str | None = None

    def __enter__(self) -> AttemptWorkdir:
        self._root = tempfile.mkdtemp(prefix="caliper-")
        os.mkdir(self.path)
        return self

    def __exit__(self, *exc: object) -> None:
        if self._root is not None:
            shutil.rmtree(self._root, ignore_errors=True)

    @property
    def path(self) -> str:
        """The workdir itself, where every step and the agent run."""
        return os.path.join(self._require_root(), "work")

    @property
    def home(self) -> str:
        """The agent's isolated home: the temp dir, beside the workdir."""
        return self._require_root()

    def _require_root(self) -> str:
        if self._root is None:
            raise RuntimeError("AttemptWorkdir used outside its `with` block")
        return self._root

    def _env(self) -> dict[str, str]:
        """The environment a step runs with: the caller's, plus both dirs.

        Deliberately the caller's own environment rather than the agent's
        isolated one: steps are the spec author's code, not the agent under
        test, and have always run with the developer's ``HOME`` and ``PATH``.
        """
        return {**os.environ, WORKDIR_ENV: self.path, SPEC_DIR_ENV: self.spec_dir}

    def run_shell(self, phase: StepPhase, cmd: str) -> StepResult:
        """Run a shell command in the workdir.

        Raises :class:`StepCancelled` when the run's cancellation killed it.
        ``cleanup`` is not killed by a cancellation already requested, so it
        still tidies up after an interrupt.
        """
        # Drain while the shell runs so verbose output cannot fill a pipe. Stop
        # when *that shell* exits: background children may keep its pipe open
        # or write forever, and must not delay the next attempt or retain disk
        # space.
        tail = bytearray()

        def keep(chunk: bytes) -> None:
            tail.extend(chunk)
            if len(tail) > _OUTPUT_TAIL:
                del tail[:-_OUTPUT_TAIL]

        with (
            subprocess.Popen(
                cmd,
                shell=True,
                cwd=self.path,
                env=self._env(),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            ) as process,
            cancel.track(process, cancel_if_requested=phase != "cleanup"),
        ):
            assert process.stdout is not None
            fd = process.stdout.fileno()
            if os.name == "nt":
                _drain_windows(process, fd, keep)
            else:
                _drain_posix(process, fd, keep)
            exit_code = process.returncode
            was_killed = cancel.was_killed(process)
        if was_killed:
            raise StepCancelled(phase)
        output = tail.decode("utf-8", errors="replace").strip()
        return StepResult(exit_code=exit_code, output=output)

    def run_python(self, phase: StepPhase, code: str) -> StepResult:
        """Run Python source in the workdir with this interpreter."""
        with tempfile.NamedTemporaryFile(
            suffix=".py", mode="w", dir=self._require_root(), delete=False
        ) as f:
            f.write(code)
            script = f.name
        try:
            result = subprocess.run(
                [sys.executable, script],
                capture_output=True,
                text=True,
                timeout=30,
                cwd=self.path,
                env=self._env(),
            )
        except subprocess.TimeoutExpired:
            return StepResult(exit_code=-1, output="", timed_out=True)
        finally:
            Path(script).unlink(missing_ok=True)
        output = (result.stderr or result.stdout).strip()
        return StepResult(exit_code=result.returncode, output=output)


def _drain_posix(process: subprocess.Popen, fd: int, keep) -> None:
    while process.poll() is None:
        if select.select([fd], [], [], 0.1)[0]:
            chunk = os.read(fd, 8192)
            if chunk:
                keep(chunk)
            else:
                process.wait()
                break
    # Drain bytes already available, with a limit so a continuously writing
    # descendant cannot keep us here indefinitely.
    for _ in range(128):
        if not select.select([fd], [], [], 0)[0]:
            break
        chunk = os.read(fd, 8192)
        if not chunk:
            break
        keep(chunk)


def _drain_windows(process: subprocess.Popen, fd: int, keep) -> None:
    # Windows select() cannot watch pipes. A reader thread drains until the
    # step exits; it never holds a disk-backed output file open.
    stopped = threading.Event()

    def drain() -> None:
        while not stopped.is_set():
            try:
                chunk = os.read(fd, 8192)
            except OSError:
                break
            if not chunk:
                break
            keep(chunk)

    reader = threading.Thread(target=drain, name="caliper-hook-output", daemon=True)
    reader.start()
    process.wait()
    reader.join(timeout=0.1)
    if not reader.is_alive():
        return
    stopped.set()
    # Closing a pipe does not reliably interrupt another thread's synchronous
    # ReadFile on Windows. Cancel that read explicitly before closing the
    # stream, then wait for the thread to exit.
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenThread.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.OpenThread.restype = wintypes.HANDLE
    kernel32.CancelSynchronousIo.argtypes = (wintypes.HANDLE,)
    kernel32.CancelSynchronousIo.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL
    thread_handle = kernel32.OpenThread(0x0001, False, reader.native_id)
    if thread_handle:
        try:
            kernel32.CancelSynchronousIo(thread_handle)
        finally:
            kernel32.CloseHandle(thread_handle)
    assert process.stdout is not None
    process.stdout.close()
    reader.join(timeout=1)
    if reader.is_alive():
        raise RuntimeError("Could not stop lifecycle hook output reader")
