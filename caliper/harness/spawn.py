"""Where a CLI harness meets the operating system.

Every process a :class:`~caliper.harness.base.CliHarness` starts goes through
one :class:`Spawner`: finding the CLI, probing it, running a short helper, and
spawning the agent itself. The default is the real one; a test hands a harness
its own, so it fakes the agent's output instead of patching ``subprocess``.

The agent spawn only. A step (``setup:``, ``assert:``, ``cleanup:``) is spawned
under different rules by :mod:`caliper.workdir`
(docs/adr/0031-the-agent-and-a-step-are-spawned-under-different-rules.md).
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import time
import uuid
from dataclasses import dataclass
from typing import IO

from caliper import cancel

_POST_KILL_DRAIN_TIMEOUT = 1


@dataclass
class ProcessResult:
    """The normalized outcome of spawning a CLI agent once."""

    stdout: str
    stderr: str
    returncode: int
    timed_out: bool
    # True when this process was killed by a cancellation rather than by its own
    # failure or the timeout. Carried so the runner can drop the attempt instead
    # of recording an interrupt as an infrastructure failure (docs/adr/0018).
    cancelled: bool = False

    @property
    def error(self) -> str | None:
        """The default ``AttemptResult.error``: a timeout marker, else stderr."""
        if self.timed_out:
            return "timeout"
        return self.stderr or None


class Spawner:
    """Starts a harness's processes for real."""

    def which(self, name: str, path: str | None = None) -> str | None:
        """The executable ``name`` resolves to on ``path`` (default ``PATH``)."""
        return shutil.which(name) if path is None else shutil.which(name, path=path)

    def version_ok(self, cli: str, *, timeout: int) -> bool:
        """True when ``cli --version`` exits 0: the CLI is installed and runnable."""
        try:
            result = self._run([cli, "--version"], timeout=timeout)
        except (OSError, subprocess.TimeoutExpired):
            return False
        return result.returncode == 0

    def capture(self, cmd: list[str], *, timeout: int) -> str | None:
        """Run a helper command, returning its stripped stdout on a clean exit."""
        try:
            result = self._run(cmd, timeout=timeout)
        except (OSError, subprocess.TimeoutExpired):
            return None
        out = result.stdout.strip()
        return out if result.returncode == 0 and out else None

    def _run(self, cmd: list[str], *, timeout: int) -> subprocess.CompletedProcess:
        """A short, uncancellable run: the probe and the helper share it."""
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)

    def execute(
        self,
        cmd: list[str],
        *,
        env: dict[str, str],
        cwd: str,
        timeout: int,
        stdin: str | None,
        label: str,
    ) -> ProcessResult:
        """Spawn the agent once, timing out into a 124/timeout ProcessResult.

        ``Popen`` rather than ``subprocess.run`` so the live process is
        registered with :mod:`caliper.cancel`: an interrupt has to be able to
        kill an agent mid-flight, or Ctrl-C waits out the full ``--timeout`` of
        every attempt already running. ``start_new_session`` gives each agent
        its own process group; cancellation also walks descendants for tools
        that create separate groups. ``label`` names the CLI in a spawn error.
        """
        process_tag = uuid.uuid4().hex
        process_env = dict(env)
        process_env[cancel.PROCESS_TAG] = process_tag
        # File-backed stdin lets communicate() be retried without abandoning
        # a partially written prompt or leaving a blocked worker thread.
        prompt_file = None
        try:
            if stdin is not None:
                prompt_file = _stage_prompt(stdin, cwd)
            with (
                subprocess.Popen(
                    cmd,
                    stdin=prompt_file
                    if prompt_file is not None
                    else subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    encoding="utf-8",
                    text=True,
                    env=process_env,
                    cwd=cwd,
                    start_new_session=True,
                ) as proc,
                cancel.track(proc, process_tag=process_tag),
            ):
                deadline = time.monotonic() + timeout
                while True:
                    try:
                        stdout, stderr = proc.communicate(
                            timeout=max(0.001, min(0.1, deadline - time.monotonic()))
                        )
                        break
                    except subprocess.TimeoutExpired as exc:
                        interrupted = cancel.requested()
                        if not interrupted and time.monotonic() < deadline:
                            continue
                        cancel.kill(proc)
                        try:
                            stdout, stderr = proc.communicate(
                                timeout=_POST_KILL_DRAIN_TIMEOUT
                            )
                        except subprocess.TimeoutExpired as drain:
                            # An untagged detached tool may still hold the
                            # pipes after its CLI parent has exited. Keep
                            # what was read before giving up on them.
                            stdout, stderr = drain.stdout, drain.stderr
                            for pipe in (proc.stdout, proc.stderr):
                                if pipe is not None:
                                    pipe.close()
                        if interrupted:
                            return ProcessResult(
                                "", "interrupted", -9, False, cancelled=True
                            )
                        return ProcessResult(
                            _timeout_output(exc.stdout, stdout),
                            _timeout_output(exc.stderr, stderr),
                            124,
                            True,
                        )
        except OSError as exc:
            return ProcessResult("", f"{label} CLI failed: {exc}", 1, False)
        finally:
            if prompt_file is not None:
                prompt_file.close()
        return ProcessResult(
            stdout=(stdout or "").strip(),
            stderr=(stderr or "").strip(),
            returncode=proc.returncode,
            timed_out=False,
            cancelled=cancel.was_killed(proc),
        )


def _timeout_output(partial: bytes | str | None, drained: bytes | str | None) -> str:
    """Keep output captured before and after killing a timed-out process.

    A second ``communicate`` returns the complete stream, including bytes read
    before timeout, with text-mode newlines already normalized. Fall back to
    ``TimeoutExpired``'s raw bytes if draining yielded nothing.
    """
    output = drained or partial or ""
    if isinstance(output, bytes):
        output = output.decode("utf-8", errors="replace")
    return output.replace("\r\n", "\n").replace("\r", "\n").strip()


def _stage_prompt(text: str, cwd: str) -> IO[bytes]:
    """Write a prompt to an unlinked temporary file, rewound for reading.

    Prefer the attempt workdir, whose volume the run already relies on; fall
    back to the system temp dir when the workdir is read-only or full.
    """
    data = text.encode("utf-8")
    try:
        return _write_temp(data, cwd)
    except OSError:
        return _write_temp(data, None)


def _write_temp(data: bytes, directory: str | None) -> IO[bytes]:
    staged = tempfile.TemporaryFile(dir=directory)  # noqa: SIM115 - caller owns it
    try:
        staged.write(data)
        staged.flush()
    except BaseException:
        staged.close()
        raise
    staged.seek(0)
    return staged
