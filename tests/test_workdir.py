"""The attempt workdir: its temp dir's lifetime, and the steps run in it.

Real processes throughout — a real shell, this interpreter — because what the
module owns is exactly how those processes are spawned, drained and stopped.
"""

from __future__ import annotations

import os
import shlex
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from caliper.workdir import AttemptWorkdir


def test_entering_makes_an_empty_workdir_beside_the_home_and_leaving_removes_both(
    tmp_path,
) -> None:
    with AttemptWorkdir(tmp_path) as workdir:
        assert os.listdir(workdir.path) == []
        assert Path(workdir.path).parent == Path(workdir.home)
        root = workdir.home
    assert not os.path.exists(root)


def test_steps_run_in_the_workdir_with_both_dirs_in_their_environment(
    tmp_path,
) -> None:
    script = (
        "import os, pathlib\n"
        "assert pathlib.Path.cwd().resolve() == "
        "pathlib.Path(os.environ['CALIPER_WORKDIR']).resolve()\n"
        f"assert os.environ['CALIPER_SPEC_DIR'] == {str(tmp_path.resolve())!r}\n"
    )
    with AttemptWorkdir(tmp_path) as workdir:
        step = workdir.run_python("assert", script)
    assert step.ok, step.output


@pytest.mark.skipif(os.name == "nt", reason="POSIX background shell syntax")
def test_hook_with_background_child_returns_after_its_shell_exits(tmp_path) -> None:
    pid_file = tmp_path / "background.pid"
    started = time.monotonic()
    try:
        with AttemptWorkdir(tmp_path) as workdir:
            step = workdir.run_shell(
                "setup",
                f"sleep 5 & echo $! > {shlex.quote(str(pid_file))}; "
                "echo setup broke >&2; exit 7",
            )
        assert time.monotonic() - started < 3
        assert step.exit_code == 7
        assert step.output == "setup broke"
    finally:
        if pid_file.exists():
            try:
                os.kill(int(pid_file.read_text().strip()), signal.SIGTERM)
            except ProcessLookupError:
                pass


@pytest.mark.skipif(os.name == "nt", reason="POSIX background shell syntax")
def test_successful_hook_with_continuous_background_writer_returns(tmp_path) -> None:
    pid_file = tmp_path / "writer.pid"
    started = time.monotonic()
    try:
        with AttemptWorkdir(tmp_path) as workdir:
            step = workdir.run_shell(
                "setup", f"yes heartbeat & echo $! > {shlex.quote(str(pid_file))}"
            )
        assert step.ok
        assert time.monotonic() - started < 3
    finally:
        if pid_file.exists():
            try:
                os.kill(int(pid_file.read_text().strip()), signal.SIGTERM)
            except ProcessLookupError:
                pass


@pytest.mark.skipif(os.name != "nt", reason="Windows background shell syntax")
def test_windows_silent_background_hook_does_not_leak_reader(tmp_path) -> None:
    before = sum(t.name == "caliper-hook-output" for t in threading.enumerate())
    child = subprocess.list2cmdline(
        [sys.executable, "-c", "import time; time.sleep(3)"]
    )
    started = time.monotonic()

    with AttemptWorkdir(tmp_path) as workdir:
        step = workdir.run_shell("setup", f'start /B "" {child}')

    assert step.ok
    assert time.monotonic() - started < 2
    assert sum(t.name == "caliper-hook-output" for t in threading.enumerate()) == before


def test_noisy_hook_keeps_only_diagnostic_tail(tmp_path) -> None:
    script = (
        'import sys; print("x" * 1000000); '
        'print("last line", file=sys.stderr); sys.exit(7)'
    )
    command = (
        subprocess.list2cmdline([sys.executable, "-c", script])
        if os.name == "nt"
        else f"{shlex.quote(sys.executable)} -c {shlex.quote(script)}"
    )
    with AttemptWorkdir(tmp_path) as workdir:
        step = workdir.run_shell("setup", command)

    assert step.exit_code == 7
    assert step.output.endswith("last line")
    assert len(step.output) <= 16000
