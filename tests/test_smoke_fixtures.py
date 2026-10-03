"""Smoke fixture lifecycles, exercised without agent CLIs or paid model calls."""

import json
import os
import signal
import socket
import sys
from contextlib import suppress
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import psutil
import pytest

from caliper.schema.spec import load_spec
from caliper.workdir import AttemptWorkdir

_SPEC_DIR = Path(__file__).parent


def test_repo_specs_do_not_write_to_shared_tmp_paths() -> None:
    root = _SPEC_DIR.parent
    paths = sorted(
        path
        for directory in ("examples", "skills", "tests")
        for path in (root / directory).rglob("*.eval.yaml")
    )
    assert paths
    for path in paths:
        spec = load_spec(path)
        for server in spec.mcp.values():
            assert not any(arg.startswith("/tmp/") for arg in server.args), path.name
        for task in spec.tasks:
            for text in (task.setup, task.cleanup, task.prompt, task.assert_script):
                assert "/tmp/" not in (text or ""), f"{path.name}: {task.name}"


@pytest.mark.parametrize(
    ("backend", "artifact"),
    [
        ("claude-code", "caliper-e2e-smoke.txt"),
        ("codex", "caliper-e2e-smoke-codex.txt"),
        ("hermes", "caliper-e2e-smoke-hermes.txt"),
        ("pi", "caliper-e2e-smoke-pi.txt"),
    ],
)
def test_backend_smoke_requires_a_fresh_artifact(
    tmp_path, monkeypatch, backend, artifact
) -> None:
    task = load_spec(_SPEC_DIR / f"{backend}-smoke.eval.yaml").tasks[0]
    assert task.assert_script is not None
    # A leftover in the caller's cwd and a successful sibling attempt must not
    # let a no-op attempt pass. Keep both attempts alive to exercise isolation.
    monkeypatch.chdir(tmp_path)
    (tmp_path / artifact).write_text("hello", encoding="utf-8")
    with AttemptWorkdir(_SPEC_DIR) as first, AttemptWorkdir(_SPEC_DIR) as second:
        for workdir in (first, second):
            assert os.listdir(workdir.path) == []
            assert task.setup is None
            assert task.cleanup is None
            missing = workdir.run_python("assert", task.assert_script)
            assert not missing.ok
            assert "file was not created" in missing.output

        output = Path(first.path) / artifact
        output.write_text("hello", encoding="utf-8")
        assert first.run_python("assert", task.assert_script).ok
        assert not second.run_python("assert", task.assert_script).ok
        sibling_path = second.path
    assert not output.exists()
    assert not Path(sibling_path).exists()
    assert (tmp_path / artifact).read_text(encoding="utf-8") == "hello"


def test_stdio_smoke_fixture_is_available_to_every_task() -> None:
    spec = load_spec(_SPEC_DIR / "mcp-smoke.eval.yaml")
    server = spec.mcp["echo"]
    # Explicitly relative MCP args are anchored to the spec directory, while
    # a bare filename would resolve in the attempt's empty cwd.
    assert server.args[0].startswith("./")
    script = (_SPEC_DIR / server.args[0]).resolve()
    request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": "secret_word"},
    }
    code = (
        "import json, subprocess, sys\n"
        f"reply = subprocess.run([sys.executable, {str(script)!r}], "
        f"input={json.dumps(request) + chr(10)!r}, text=True, "
        "capture_output=True, check=True)\n"
        "assert json.loads(reply.stdout)['result']['content'][0]['text'] == 'caliper'\n"
    )
    for task in spec.tasks:
        with AttemptWorkdir(_SPEC_DIR) as workdir:
            assert task.setup is None
            assert task.cleanup is None
            step = workdir.run_python("assert", code)
            assert step.ok, step.output
            assert os.listdir(workdir.path) == []


@pytest.mark.skipif(os.name == "nt", reason="HTTP smoke fixture requires POSIX shell")
def test_http_smoke_keeps_pid_and_log_in_its_workdir(monkeypatch) -> None:
    task = load_spec(_SPEC_DIR / "mcp-header-smoke.eval.yaml").tasks[0]
    assert task.setup is not None
    assert task.cleanup is not None
    monkeypatch.setenv("MCP_ECHO_TOKEN", "fixture-test-token")
    # The manual eval retains a fixed port; its lifecycle test uses a free port
    # so it can run alongside that eval without stealing its listener.
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    with AttemptWorkdir(_SPEC_DIR) as workdir:
        # HTTPServer's reverse lookup can stall before listen() on macOS CI.
        # Exercise the real fixture entry point with DNS forbidden, so the
        # test stays sensitive to that startup regression on every platform.
        launcher = Path(workdir.home) / "http-launcher.py"
        launcher.write_text(
            "import runpy, socket, sys\n"
            "def no_reverse_dns(*args):\n"
            "    raise AssertionError('HTTP fixture must not resolve hostnames')\n"
            "socket.getfqdn = no_reverse_dns\n"
            "fixture = sys.argv.pop(1)\n"
            "runpy.run_path(fixture, run_name='__main__')\n",
            encoding="utf-8",
        )
        setup = task.setup.replace("8765", str(port)).replace(
            "python3", f'"{sys.executable}" "{launcher}"'
        )
        pid_file = Path(workdir.path) / "caliper-echo-http.pid"
        log_file = Path(workdir.path) / "caliper-echo-http.log"
        server_process = None
        try:
            step = workdir.run_shell("setup", setup)
            assert step.ok, step.output
            assert pid_file.exists()
            assert log_file.exists()
            assert psutil.pid_exists(int(pid_file.read_text())), log_file.read_text()
            server_process = psutil.Process(int(pid_file.read_text()))
            payload = json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": "secret_word"},
                }
            ).encode()
            url = f"http://127.0.0.1:{port}/mcp"
            with pytest.raises(HTTPError) as denied:
                urlopen(Request(url, data=payload), timeout=5)
            assert denied.value.code == 401
            denied.value.close()
            request = Request(
                url,
                data=payload,
                headers={"Authorization": "Bearer fixture-test-token"},
            )
            with urlopen(request, timeout=5) as response:
                assert json.load(response)["result"]["content"][0]["text"] == "caliper"
        finally:
            cleanup = workdir.run_shell("cleanup", task.cleanup)
            if not cleanup.ok and pid_file.exists():
                with suppress(ProcessLookupError):
                    os.kill(int(pid_file.read_text()), signal.SIGTERM)
        assert cleanup.ok, cleanup.output
        assert server_process is not None
        server_process.wait(timeout=5)
    assert not pid_file.exists()
    assert not log_file.exists()
