from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

import pytest
import psutil
from pydantic import ValidationError

from caliper import cancel
from caliper.harness.base import (
    AttemptResult,
    CliHarness,
    ConversationTurn,
    HarnessBackend,
    HarnessConfigurationError,
    RunContext,
)
from caliper.harness.mcp import (
    McpPreflightInterrupted,
    preflight_stdio_servers,
    resolve_servers,
)
from caliper.judge.base import JudgeResult
from caliper.runner import RunAborted, run
from caliper.schema.results import Outcome
from caliper.schema.spec import EvalSpec, McpServer, TaskSpec

from conftest import run_context


# --- schema validation ----------------------------------------------------


def test_mcp_block_parses_stdio_server() -> None:
    spec = EvalSpec.model_validate(
        {
            "mcp": {
                "weather": {
                    "command": "python3",
                    "args": ["server.py"],
                    "env": {"API_TOKEN": "${MCP_API_TOKEN}"},
                }
            },
            "tasks": [{"name": "t", "prompt": "p", "expect": "e"}],
        }
    )
    server = spec.mcp["weather"]
    assert isinstance(server, McpServer)
    assert server.command == "python3"
    assert server.args == ["server.py"]
    # ${VAR} is kept literal at load; it is resolved only at materialization.
    assert server.env == {"API_TOKEN": "${MCP_API_TOKEN}"}


def test_mcp_defaults_are_empty() -> None:
    spec = EvalSpec.model_validate(
        {"tasks": [{"name": "t", "prompt": "p", "expect": "e"}]}
    )
    assert spec.mcp == {}


def test_mcp_server_defaults_args_and_env() -> None:
    server = McpServer.model_validate({"command": "python3"})
    assert server.args == []
    assert server.env == {}


@pytest.mark.parametrize("bad_name", ["wea ther", "we/ather", "wea.ther", ""])
def test_mcp_rejects_bad_server_name(bad_name: str) -> None:
    with pytest.raises(ValidationError, match="invalid MCP server name"):
        EvalSpec.model_validate(
            {
                "mcp": {bad_name: {"command": "python3"}},
                "tasks": [{"name": "t", "prompt": "p", "expect": "e"}],
            }
        )


def test_mcp_rejects_unknown_key() -> None:
    # A typo or unsupported key must error clearly (extra="forbid").
    with pytest.raises(ValidationError):
        EvalSpec.model_validate(
            {
                "mcp": {"weather": {"command": "python3", "bogus": "x"}},
                "tasks": [{"name": "t", "prompt": "p", "expect": "e"}],
            }
        )


# --- remote (http/sse) transport ------------------------------------------


@pytest.mark.parametrize("transport", ["http", "sse"])
def test_mcp_block_parses_remote_server(transport: str) -> None:
    spec = EvalSpec.model_validate(
        {
            "mcp": {
                "gdrive": {
                    "type": transport,
                    "url": "https://mcp.example.com/gdrive",
                    "headers": {"Authorization": "Bearer ${GDRIVE_TOKEN}"},
                }
            },
            "tasks": [{"name": "t", "prompt": "p", "expect": "e"}],
        }
    )
    server = spec.mcp["gdrive"]
    assert server.is_remote
    assert server.type == transport
    assert server.url == "https://mcp.example.com/gdrive"
    # ${VAR} is kept literal at load; it is resolved only at materialization.
    assert server.headers == {"Authorization": "Bearer ${GDRIVE_TOKEN}"}


def test_mcp_remote_defaults_empty_headers() -> None:
    server = McpServer.model_validate({"type": "http", "url": "https://x/mcp"})
    assert server.headers == {}
    assert server.is_remote


def test_mcp_stdio_is_default_and_not_remote() -> None:
    server = McpServer.model_validate({"command": "python3"})
    assert server.type == "stdio"
    assert not server.is_remote


def test_mcp_remote_requires_url() -> None:
    with pytest.raises(ValidationError, match="requires a non-empty url"):
        McpServer.model_validate({"type": "http"})


def test_mcp_remote_rejects_stdio_fields() -> None:
    with pytest.raises(ValidationError, match="stdio-only fields"):
        McpServer.model_validate(
            {"type": "http", "url": "https://x/mcp", "command": "python3"}
        )


def test_mcp_stdio_rejects_remote_fields() -> None:
    with pytest.raises(ValidationError, match="remote-only"):
        McpServer.model_validate({"command": "python3", "url": "https://x/mcp"})


def test_mcp_rejects_unknown_transport_type() -> None:
    with pytest.raises(ValidationError, match="invalid MCP server type"):
        McpServer.model_validate({"type": "grpc", "url": "https://x/mcp"})


def test_mcp_rejects_blank_command() -> None:
    with pytest.raises(ValidationError, match="non-empty"):
        EvalSpec.model_validate(
            {
                "mcp": {"weather": {"command": "   "}},
                "tasks": [{"name": "t", "prompt": "p", "expect": "e"}],
            }
        )


def test_mcp_rejects_missing_command() -> None:
    with pytest.raises(ValidationError):
        EvalSpec.model_validate(
            {
                "mcp": {"weather": {"args": ["x"]}},
                "tasks": [{"name": "t", "prompt": "p", "expect": "e"}],
            }
        )


# --- shared resolution (harness/mcp.py) ------------------------------------

_TOOLS_REPLY = (
    "sys.stdin.readline()\n"  # notifications/initialized
    "request = json.loads(sys.stdin.readline())\n"
    "print(json.dumps({'jsonrpc': '2.0', 'id': request['id'], "
    "'result': {'tools': []}}), flush=True)\n"
    "sys.stdin.readline()\n"
)
_TOOL_INITIALIZATION = repr(
    {
        "protocolVersion": "2025-03-26",
        "capabilities": {"tools": {}},
        "serverInfo": {"name": "test", "version": "1"},
    }
)
_RESOURCE_INITIALIZATION = repr(
    {
        "protocolVersion": "2025-03-26",
        "capabilities": {"resources": {}},
        "serverInfo": {"name": "test", "version": "1"},
    }
)
_EMPTY_INITIALIZATION = repr(
    {
        "protocolVersion": "2025-03-26",
        "capabilities": {},
        "serverInfo": {"name": "test", "version": "1"},
    }
)


def test_resolve_servers_interpolates_stdio_env(monkeypatch) -> None:
    monkeypatch.setenv("MCP_API_TOKEN", "sk-secret")
    declared = {
        "weather": McpServer(
            command="python3", args=["server.py"], env={"API_TOKEN": "${MCP_API_TOKEN}"}
        )
    }

    resolved = resolve_servers(declared)["weather"]

    assert not resolved.is_remote
    assert resolved.command == "python3"
    assert resolved.args == ["server.py"]
    assert resolved.env == {"API_TOKEN": "sk-secret"}
    assert resolved.entry() == {
        "command": "python3",
        "args": ["server.py"],
        "env": {"API_TOKEN": "sk-secret"},
    }


def test_resolve_servers_interpolates_remote_url_and_headers(monkeypatch) -> None:
    monkeypatch.setenv("GDRIVE_TOKEN", "tok-123")
    monkeypatch.setenv("GDRIVE_HOST", "mcp.example.com")
    declared = {
        "gdrive": McpServer(
            type="sse",
            url="https://${GDRIVE_HOST}/gdrive",
            headers={"Authorization": "Bearer ${GDRIVE_TOKEN}"},
        )
    }

    resolved = resolve_servers(declared)["gdrive"]

    assert resolved.is_remote
    assert resolved.type == "sse"
    assert resolved.entry() == {
        "url": "https://mcp.example.com/gdrive",
        "headers": {"Authorization": "Bearer tok-123"},
    }


def test_resolve_servers_entry_omits_empty_optionals() -> None:
    declared = {
        "bare": McpServer(command="python3"),
        "remote": McpServer(type="http", url="https://x/mcp"),
    }

    entries = {name: r.entry() for name, r in resolve_servers(declared).items()}

    assert entries["bare"] == {"command": "python3"}
    assert entries["remote"] == {"url": "https://x/mcp"}


def test_resolve_servers_unset_var_fails_at_the_boundary(monkeypatch) -> None:
    monkeypatch.delenv("MCP_MISSING_TOKEN", raising=False)
    declared = {
        "weather": McpServer(command="python3", env={"TOKEN": "${MCP_MISSING_TOKEN}"})
    }

    with pytest.raises(HarnessConfigurationError, match="MCP_MISSING_TOKEN"):
        resolve_servers(declared)


def test_resolve_servers_handles_no_declaration() -> None:
    assert resolve_servers(None) == {}
    assert resolve_servers({}) == {}


def test_run_anchors_explicit_mcp_paths_to_spec_directory(
    tmp_path, monkeypatch
) -> None:
    spec_dir = tmp_path / "specs"
    spec_dir.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    spec_path = spec_dir / "m.eval.yaml"
    spec_path.write_text("tasks: []\n")
    spec = _spec_with_mcp()
    spec.mcp = {
        "echo": McpServer(
            command="./bin/server",
            args=["./servers/weather.py", "../shared/data", "bare", "/absolute"],
        )
    }
    harness = _McpHarness()

    run(spec, spec_path, harness, _PassJudge(), k=1, workers=1)

    assert harness.seen["echo"].command == str(spec_dir / "bin/server")
    assert harness.seen["echo"].args == [
        str(spec_dir / "servers/weather.py"),
        str(tmp_path / "shared/data"),
        "bare",
        "/absolute",
    ]
    assert spec.mcp["echo"].args[0] == "./servers/weather.py"


def test_preflight_initializes_a_local_server(tmp_path) -> None:
    script = tmp_path / "server.py"
    script.write_text(
        "import json, sys\n"
        "request = json.loads(sys.stdin.readline())\n"
        "print(json.dumps({'jsonrpc': '2.0', 'id': request['id'], "
        "'result': {'protocolVersion': '2025-03-26', 'capabilities': {'tools': {}}, "
        "'serverInfo': {'name': 'test', 'version': '1'}}}), flush=True)\n"
        + _TOOLS_REPLY
    )
    preflight_stdio_servers(
        {"echo": McpServer(command=sys.executable, args=[str(script)])}
    )


def test_preflight_accepts_bundled_echo_server_protocol_version() -> None:
    # echo_server.py answers 2024-11-05, not the version preflight proposes.
    script = Path(__file__).parent / "fixtures" / "mcp" / "echo_server.py"
    preflight_stdio_servers(
        {"echo": McpServer(command=sys.executable, args=[str(script)])}
    )


def test_preflight_does_not_inspect_unrelated_process_environments(
    tmp_path, monkeypatch
) -> None:
    script = tmp_path / "server.py"
    script.write_text(
        "import json, sys\n"
        "request = json.loads(sys.stdin.readline())\n"
        "print(json.dumps({'jsonrpc': '2.0', 'id': request['id'], "
        f"'result': {_TOOL_INITIALIZATION}}}), flush=True)\n" + _TOOLS_REPLY
    )

    def no_global_scan():
        raise AssertionError("preflight scanned unrelated processes")

    monkeypatch.setattr(psutil, "process_iter", no_global_scan)
    preflight_stdio_servers(
        {"echo": McpServer(command=sys.executable, args=[str(script)])}
    )


def test_preflight_ignores_notifications_before_responses(tmp_path) -> None:
    script = tmp_path / "notifying.py"
    script.write_text(
        "import json, sys\n"
        "request = json.loads(sys.stdin.readline())\n"
        "print(json.dumps({'jsonrpc': '2.0', 'method': 'notifications/message', "
        "'params': {'level': 'info', 'data': 'starting'}}), flush=True)\n"
        "print(json.dumps({'jsonrpc': '2.0', 'id': request['id'], "
        f"'result': {_TOOL_INITIALIZATION}}}), flush=True)\n"
        "sys.stdin.readline()\n"
        "request = json.loads(sys.stdin.readline())\n"
        "print(json.dumps({'jsonrpc': '2.0', 'method': 'notifications/message', "
        "'params': {'level': 'info', 'data': 'ready'}}), flush=True)\n"
        "print(json.dumps({'jsonrpc': '2.0', 'id': request['id'], "
        "'result': {'tools': []}}), flush=True)\n"
        "sys.stdin.readline()\n"
    )

    preflight_stdio_servers(
        {"echo": McpServer(command=sys.executable, args=[str(script)])}
    )


def test_preflight_answers_server_ping_with_colliding_request_id(tmp_path) -> None:
    script = tmp_path / "ping.py"
    script.write_text(
        "import json, sys\n"
        "request = json.loads(sys.stdin.readline())\n"
        "print(json.dumps({'jsonrpc': '2.0', 'id': request['id'], "
        "'method': 'ping'}), flush=True)\n"
        "reply = json.loads(sys.stdin.readline())\n"
        "if reply != {'jsonrpc': '2.0', 'id': request['id'], 'result': {}}: "
        "sys.exit(2)\n"
        "print(json.dumps({'jsonrpc': '2.0', 'id': request['id'], "
        f"'result': {_TOOL_INITIALIZATION}}}), flush=True)\n" + _TOOLS_REPLY
    )

    preflight_stdio_servers(
        {"echo": McpServer(command=sys.executable, args=[str(script)])}
    )


def test_preflight_accepts_resource_only_server(tmp_path) -> None:
    script = tmp_path / "resources.py"
    script.write_text(
        "import json, sys\n"
        "request = json.loads(sys.stdin.readline())\n"
        "print(json.dumps({'jsonrpc': '2.0', 'id': request['id'], "
        f"'result': {_RESOURCE_INITIALIZATION}}}), flush=True)\n"
        "sys.stdin.readline()\n"
        "request = sys.stdin.readline()\n"
        "if request:\n"
        "    print(json.dumps({'jsonrpc': '2.0', 'id': json.loads(request)['id'], "
        "'error': {'code': -32601, 'message': 'Method not found'}}), flush=True)\n"
    )

    preflight_stdio_servers(
        {"resources": McpServer(command=sys.executable, args=[str(script)])}
    )


def test_preflight_kills_server_child_after_launcher_exits(tmp_path) -> None:
    marker = tmp_path / "child-pid"
    child_code = (
        "import os, pathlib, signal, sys, time\n"
        + (
            "signal.signal(signal.SIGTERM, lambda *_: None)\n"
            if os.name == "posix"
            else ""
        )
        + "pathlib.Path(sys.argv[1]).write_text(str(os.getpid()))\n"
        "time.sleep(30)\n"
    )
    script = tmp_path / "launcher.py"
    script.write_text(
        "import json, pathlib, subprocess, sys, time\n"
        f"subprocess.Popen([sys.executable, '-c', {child_code!r}, sys.argv[1]], "
        "stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
        "while not pathlib.Path(sys.argv[1]).exists(): time.sleep(0.01)\n"
        "request = json.loads(sys.stdin.readline())\n"
        "print(json.dumps({'jsonrpc': '2.0', 'id': request['id'], "
        f"'result': {_TOOL_INITIALIZATION}}}), flush=True)\n" + _TOOLS_REPLY
    )
    pid = None
    try:
        preflight_stdio_servers(
            {"echo": McpServer(command=sys.executable, args=[str(script), str(marker)])}
        )
        pid = int(marker.read_text())
        try:
            child = psutil.Process(pid)
        except psutil.NoSuchProcess:
            pass
        else:
            assert not child.is_running() or child.status() == psutil.STATUS_ZOMBIE, (
                "server child survived preflight"
            )
    finally:
        if pid is None and marker.exists():
            pid = int(marker.read_text())
        if pid is not None:
            try:
                psutil.Process(pid).kill()
            except psutil.NoSuchProcess:
                pass


def test_preflight_uses_sandbox_extra_path_for_command(tmp_path) -> None:
    command = tmp_path / "mcp-server"
    command.write_text(
        f"#!{sys.executable}\n"
        "import json, sys\n"
        "request = json.loads(sys.stdin.readline())\n"
        "print(json.dumps({'jsonrpc': '2.0', 'id': request['id'], "
        f"'result': {_TOOL_INITIALIZATION}}}), flush=True)\n" + _TOOLS_REPLY
    )
    command.chmod(0o755)

    preflight_stdio_servers(
        {"echo": McpServer(command="mcp-server")}, extra_path=[str(tmp_path)]
    )


def test_preflight_does_not_inherit_host_only_variables(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CALIPER_HOST_ONLY_SECRET", "not-in-attempt")
    script = tmp_path / "server.py"
    script.write_text(
        "import json, os, sys\n"
        "if os.getenv('CALIPER_HOST_ONLY_SECRET'): sys.exit(2)\n"
        "request = json.loads(sys.stdin.readline())\n"
        "print(json.dumps({'jsonrpc': '2.0', 'id': request['id'], "
        f"'result': {_TOOL_INITIALIZATION}}}), flush=True)\n" + _TOOLS_REPLY
    )

    preflight_stdio_servers(
        {"echo": McpServer(command=sys.executable, args=[str(script)])}
    )


class _AttemptPreflightHarness(CliHarness):
    supports_mcp = True

    @property
    def name(self) -> str:
        return "attempt-preflight"

    def skills_root(self, ctx: RunContext) -> Path:
        return Path(ctx.isolated_home) / "skills"

    def _command(self, ctx: RunContext):
        return (
            [sys.executable, "-c", "print('ok')"],
            None,
            None,
        )

    def _environment(self, ctx: RunContext) -> dict[str, str]:
        return self._isolated_env(ctx)

    def _parse_stream(self, stdout: str):
        return [
            ConversationTurn(role="assistant", content=stdout.strip())
        ], stdout.strip()


def test_server_that_dies_after_initial_preflight_stops_before_agent(tmp_path) -> None:
    marker = tmp_path / "started"
    script = tmp_path / "server.py"
    script.write_text(
        "import json, pathlib, sys\n"
        "marker = pathlib.Path(sys.argv[1])\n"
        "if marker.exists(): sys.exit(3)\n"
        "marker.write_text('started')\n"
        "request = json.loads(sys.stdin.readline())\n"
        "print(json.dumps({'jsonrpc': '2.0', 'id': request['id'], "
        f"'result': {_TOOL_INITIALIZATION}}}), flush=True)\n" + _TOOLS_REPLY
    )
    servers = {
        "echo": McpServer(command=sys.executable, args=[str(script), str(marker)])
    }
    preflight_stdio_servers(servers)
    home = tmp_path / "home"
    workdir = home / "work"
    workdir.mkdir(parents=True)
    ctx = run_context(
        isolated_home=str(home), workdir=str(workdir), mcp_servers=servers
    )

    with pytest.raises(HarnessConfigurationError, match="MCP server 'echo'"):
        _AttemptPreflightHarness().run(ctx)


def test_preflight_rejects_server_exiting_after_initialize(tmp_path) -> None:
    script = tmp_path / "one_reply.py"
    script.write_text(
        "import json, sys\n"
        "request = json.loads(sys.stdin.readline())\n"
        "print(json.dumps({'jsonrpc': '2.0', 'id': request['id'], "
        f"'result': {_EMPTY_INITIALIZATION}}}), flush=True)\n"
    )

    with pytest.raises(HarnessConfigurationError, match="MCP server 'echo'"):
        preflight_stdio_servers(
            {"echo": McpServer(command=sys.executable, args=[str(script)])}
        )


@pytest.mark.parametrize(
    "result",
    [
        {},
        {
            "protocolVersion": "unsupported",
            "capabilities": {},
            "serverInfo": {"name": "test", "version": "1"},
        },
    ],
)
def test_preflight_rejects_invalid_initialization(tmp_path, result) -> None:
    script = tmp_path / "invalid.py"
    script.write_text(
        "import json, sys\n"
        "request = json.loads(sys.stdin.readline())\n"
        f"print(json.dumps({{'jsonrpc': '2.0', 'id': request['id'], 'result': {result!r}}}), flush=True)\n"
        "sys.stdin.readline()\n"
    )

    with pytest.raises(HarnessConfigurationError, match="invalid initialization"):
        preflight_stdio_servers(
            {"echo": McpServer(command=sys.executable, args=[str(script)])}
        )


def test_setup_can_stage_an_mcp_server_before_attempt_preflight(tmp_path) -> None:
    template = tmp_path / "server-template.py"
    template.write_text(
        "import json, sys\n"
        "request = json.loads(sys.stdin.readline())\n"
        "print(json.dumps({'jsonrpc': '2.0', 'id': request['id'], "
        f"'result': {_TOOL_INITIALIZATION}}}), flush=True)\n" + _TOOLS_REPLY
    )
    staged = tmp_path / "staged-server.py"
    stage_script = tmp_path / "stage.py"
    stage_script.write_text(
        "from pathlib import Path\n"
        "import shutil\n"
        "here = Path(__file__).parent\n"
        "shutil.copyfile(here / 'server-template.py', here / 'staged-server.py')\n"
    )
    spec_path = tmp_path / "m.eval.yaml"
    spec_path.write_text("tasks: []\n")
    spec = EvalSpec(
        mcp={"echo": McpServer(command=sys.executable, args=[str(staged)])},
        tasks=[
            TaskSpec(
                id="task-001",
                name="staged server",
                prompt="use it",
                setup=f'"{sys.executable}" "{stage_script}"',
                assert_script="assert True",
            )
        ],
    )

    results = run(spec, spec_path, _AttemptPreflightHarness(), _PassJudge(), k=1)

    assert staged.exists()
    assert results.task_results[0].attempts[0].outcome == Outcome.PASS


def test_cancel_interrupts_stalled_preflight_and_skips_next_server(
    tmp_path,
) -> None:
    ready = tmp_path / "ready"
    second = tmp_path / "second"
    script = tmp_path / "stalled.py"
    script.write_text(
        "from pathlib import Path\n"
        "import sys, time\n"
        "Path(sys.argv[1]).write_text('started')\n"
        "time.sleep(30)\n"
    )
    servers = {
        "first": McpServer(command=sys.executable, args=[str(script), str(ready)]),
        "second": McpServer(command=sys.executable, args=[str(script), str(second)]),
    }
    errors: list[Exception] = []

    def check() -> None:
        try:
            preflight_stdio_servers(servers, timeout=5)
        except Exception as exc:
            errors.append(exc)

    cancel.reset()
    thread = threading.Thread(target=check)
    thread.start()
    try:
        deadline = time.monotonic() + 3
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert ready.exists()
        cancel.request()
        thread.join(timeout=2)
        assert not thread.is_alive(), "Ctrl-C waited for the preflight timeout"
        assert errors and isinstance(errors[0], McpPreflightInterrupted)
        assert not second.exists()
    finally:
        cancel.request()
        thread.join(timeout=2)
        cancel.reset()


def test_interrupt_during_preflight_returns_an_interrupted_run(tmp_path) -> None:
    ready = tmp_path / "ready"
    script = tmp_path / "stalled.py"
    script.write_text(
        "from pathlib import Path\n"
        "import sys, time\n"
        "Path(sys.argv[1]).write_text('started')\n"
        "time.sleep(30)\n"
    )
    spec_path = tmp_path / "m.eval.yaml"
    spec_path.write_text("tasks: []\n")
    spec = EvalSpec(
        mcp={"slow": McpServer(command=sys.executable, args=[str(script), str(ready)])},
        tasks=[
            TaskSpec(
                id="task-001",
                name="slow server",
                prompt="use it",
                assert_script="assert True",
            )
        ],
    )
    box: dict[str, object] = {}

    def execute() -> None:
        try:
            box["result"] = run(
                spec, spec_path, _AttemptPreflightHarness(), _PassJudge(), k=1
            )
        except Exception as exc:
            box["error"] = exc

    thread = threading.Thread(target=execute)
    thread.start()
    try:
        deadline = time.monotonic() + 3
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert ready.exists()
        cancel.request()
        thread.join(timeout=3)
        assert not thread.is_alive()
        assert "error" not in box
        assert box["result"].run.interrupted
        assert box["result"].task_results[0].attempts == []
    finally:
        cancel.request()
        thread.join(timeout=3)
        cancel.reset()


def test_preflight_waits_for_a_slow_server_within_its_timeout(tmp_path) -> None:
    script = tmp_path / "slow.py"
    script.write_text(
        "import json, sys, time\n"
        "request = json.loads(sys.stdin.readline())\n"
        "time.sleep(0.5)\n"
        "print(json.dumps({'jsonrpc': '2.0', 'id': request['id'], "
        f"'result': {_EMPTY_INITIALIZATION}}}), flush=True)\n"
        "sys.stdin.readline()\n"
        "sys.stdin.readline()\n"
    )
    server = {"echo": McpServer(command=sys.executable, args=[str(script)])}

    with pytest.raises(HarnessConfigurationError, match="within 0.2 seconds"):
        preflight_stdio_servers(server, timeout=0.2)
    preflight_stdio_servers(server, timeout=5)


def test_preflight_shares_one_deadline_across_servers(tmp_path) -> None:
    script = tmp_path / "slow.py"
    script.write_text(
        "import json, sys, time\n"
        "request = json.loads(sys.stdin.readline())\n"
        "time.sleep(0.6)\n"
        "print(json.dumps({'jsonrpc': '2.0', 'id': request['id'], "
        f"'result': {_EMPTY_INITIALIZATION}}}), flush=True)\n"
        "sys.stdin.readline()\n"
        "sys.stdin.readline()\n"
    )
    server = McpServer(command=sys.executable, args=[str(script)])

    # Each server fits in 1s alone; together they exceed it.
    with pytest.raises(HarnessConfigurationError, match="MCP server 'second'"):
        preflight_stdio_servers({"first": server, "second": server}, timeout=1)


def test_preflight_times_out_when_server_stops_reading_stdin(tmp_path) -> None:
    # Floods ping requests without reading replies, so preflight's writes fill
    # the stdin pipe and block.
    script = tmp_path / "flood.py"
    script.write_text(
        "import json, sys\n"
        "i = 0\n"
        "while True:\n"
        "    i += 1\n"
        "    print(json.dumps({'jsonrpc': '2.0', 'id': f'p{i}', 'method': 'ping', "
        "'params': {'pad': 'x' * 4096}}), flush=True)\n"
    )
    start = time.monotonic()
    with pytest.raises(HarnessConfigurationError, match="did not answer"):
        preflight_stdio_servers(
            {"echo": McpServer(command=sys.executable, args=[str(script)])},
            timeout=1,
        )
    assert time.monotonic() - start < 5


def test_readme_relative_mcp_arg_starts_from_any_cwd(tmp_path, monkeypatch) -> None:
    spec_dir = tmp_path / "specs"
    server_dir = spec_dir / "servers"
    server_dir.mkdir(parents=True)
    script = server_dir / "weather.py"
    script.write_text(
        "import json, sys\n"
        "request = json.loads(sys.stdin.readline())\n"
        "print(json.dumps({'jsonrpc': '2.0', 'id': request['id'], "
        f"'result': {_TOOL_INITIALIZATION}}}), flush=True)\n" + _TOOLS_REPLY
    )
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    spec_path = spec_dir / "weather.eval.yaml"
    spec_path.write_text("tasks: []\n")
    spec = _spec_with_mcp()
    spec.mcp = {
        "weather": McpServer(command=sys.executable, args=["./servers/weather.py"])
    }
    harness = _McpHarness()

    run(spec, spec_path, harness, _PassJudge(), k=1, workers=1)

    assert harness.seen["weather"].args == [str(script)]


@pytest.mark.parametrize(
    "script", ["raise RuntimeError('broken')\n", "import time; time.sleep(30)\n"]
)
def test_dead_server_stops_run_before_any_attempt(tmp_path, script) -> None:
    server = tmp_path / "broken.py"
    server.write_text(script)
    spec_path = tmp_path / "m.eval.yaml"
    spec_path.write_text("tasks: []\n")
    spec = _spec_with_mcp()
    spec.mcp = {"broken": McpServer(command=sys.executable, args=[str(server)])}

    # The attempt's --timeout bounds its preflight.
    with pytest.raises(RunAborted, match="MCP server 'broken'") as exc:
        run(
            spec,
            spec_path,
            _AttemptPreflightHarness(),
            _PassJudge(),
            k=1,
            workers=1,
            timeout=1,
        )
    assert exc.value.results.task_results[0].attempts == []


def test_missing_server_command_stops_run_before_any_attempt(tmp_path) -> None:
    spec_path = tmp_path / "m.eval.yaml"
    spec_path.write_text("tasks: []\n")
    spec = _spec_with_mcp()
    spec.mcp = {"missing": McpServer(command="./missing-server")}

    with pytest.raises(RunAborted, match="MCP server 'missing'") as exc:
        run(spec, spec_path, _AttemptPreflightHarness(), _PassJudge(), k=1, workers=1)
    assert exc.value.results.task_results[0].attempts == []


# --- run-seam capability guard --------------------------------------------


class _NoMcpHarness(HarnessBackend):
    @property
    def name(self) -> str:
        return "nomcp"

    def run(self, *args, **kwargs) -> AttemptResult:  # pragma: no cover - never runs
        raise AssertionError("run() must not be reached when the guard fires")


class _ByDesignNoMcpHarness(HarnessBackend):
    mcp_unsupported_hint = "Expose it as a CLI tool the skill drives instead."

    @property
    def name(self) -> str:
        return "bydesign"

    def run(self, *args, **kwargs) -> AttemptResult:  # pragma: no cover - never runs
        raise AssertionError("run() must not be reached when the guard fires")


class _NoMcpRunnableHarness(HarnessBackend):
    """No MCP support, but it runs — for the ablate-every-server case."""

    def __init__(self) -> None:
        self.seen: dict | None = None

    @property
    def name(self) -> str:
        return "nomcp"

    def run(self, ctx: RunContext) -> AttemptResult:
        self.seen = ctx.mcp_servers
        return AttemptResult(
            transcript=[],
            final_output="ok",
            exit_code=0,
            duration_seconds=0.1,
        )


class _McpHarness(HarnessBackend):
    supports_mcp = True

    def __init__(self) -> None:
        self.seen: dict | None = None

    @property
    def name(self) -> str:
        return "yesmcp"

    def run(self, ctx: RunContext) -> AttemptResult:
        self.seen = ctx.mcp_servers
        return AttemptResult(
            transcript=[],
            final_output="ok",
            exit_code=0,
            duration_seconds=0.1,
        )


class _PassJudge:
    backend = "test"
    model = None

    def evaluate(
        self, task, transcript, final_output, spec_dir, workdir
    ) -> JudgeResult:
        return JudgeResult(passed=True, reasoning="ok")


def _spec_with_mcp() -> EvalSpec:
    return EvalSpec(
        mcp={"echo": McpServer(command="python3", args=["s.py"])},
        tasks=[
            TaskSpec(id="task-001", name="t", prompt="p", assert_script="assert True")
        ],
    )


def test_guard_refuses_mcp_spec_on_unsupported_backend(tmp_path) -> None:
    spec_path = tmp_path / "m.eval.yaml"
    spec_path.write_text("tasks: []\n")
    # A backend without a hint (a not-yet-implemented slice) gets the generic
    # "not supported yet" message.
    with pytest.raises(
        HarnessConfigurationError, match="does not support MCP yet"
    ) as exc:
        run(
            spec=_spec_with_mcp(),
            spec_path=spec_path,
            harness=_NoMcpHarness(),
            judge=_PassJudge(),
            k=1,
            workers=1,
            timeout=30,
        )
    assert "in this release" in str(exc.value)


def test_guard_refusal_uses_backend_hint_when_present(tmp_path) -> None:
    spec_path = tmp_path / "m.eval.yaml"
    spec_path.write_text("tasks: []\n")
    # A backend whose lack of MCP is permanent-by-design supplies its own hint,
    # which the refusal carries verbatim — and drops the misleading "yet".
    with pytest.raises(HarnessConfigurationError) as exc:
        run(
            spec=_spec_with_mcp(),
            spec_path=spec_path,
            harness=_ByDesignNoMcpHarness(),
            judge=_PassJudge(),
            k=1,
            workers=1,
            timeout=30,
        )
    message = str(exc.value)
    assert "Expose it as a CLI tool the skill drives instead." in message
    assert "does not support MCP yet" not in message


def test_guard_allows_mcp_spec_on_supporting_backend(tmp_path) -> None:
    spec_path = tmp_path / "m.eval.yaml"
    spec_path.write_text("tasks: []\n")
    harness = _McpHarness()
    run(
        spec=_spec_with_mcp(),
        spec_path=spec_path,
        harness=harness,
        judge=_PassJudge(),
        k=1,
        workers=1,
        timeout=30,
    )
    # The runner threads the declared McpServer models straight to the backend.
    assert harness.seen == {"echo": McpServer(command="python3", args=["s.py"])}


def _spec_without_mcp() -> EvalSpec:
    return EvalSpec(
        tasks=[
            TaskSpec(id="task-001", name="t", prompt="p", assert_script="assert True")
        ]
    )


def test_an_explicitly_empty_mcp_block_still_isolates(tmp_path) -> None:
    # `mcp: {}` declares the block with no servers, so the backend must see an
    # empty mapping (zero servers), not None (the CLI's ambient config).
    spec_path = tmp_path / "m.eval.yaml"
    spec_path.write_text("tasks: []\n")
    harness = _McpHarness()
    run(
        spec=EvalSpec(
            mcp={},
            tasks=[
                TaskSpec(
                    id="task-001", name="t", prompt="p", assert_script="assert True"
                )
            ],
        ),
        spec_path=spec_path,
        harness=harness,
        judge=_PassJudge(),
        k=1,
        workers=1,
        timeout=30,
    )
    assert harness.seen == {}


def test_an_omitted_mcp_block_leaves_the_backend_config_alone(tmp_path) -> None:
    spec_path = tmp_path / "m.eval.yaml"
    spec_path.write_text("tasks: []\n")
    harness = _McpHarness()
    run(
        spec=_spec_without_mcp(),
        spec_path=spec_path,
        harness=harness,
        judge=_PassJudge(),
        k=1,
        workers=1,
        timeout=30,
    )
    assert harness.seen is None


def test_ablating_every_server_runs_on_a_backend_without_mcp(tmp_path) -> None:
    # The guard exists so declared tools are not silently absent. With every
    # server ablated the absence is the caller's explicit choice, recorded in
    # RunMeta.ablated, so the spec is runnable on a backend that cannot honor it.
    # The backend still gets an empty mapping, not None: the block was declared,
    # so a supporting backend must isolate to zero servers rather than fall back
    # to its ambient config.
    spec_path = tmp_path / "m.eval.yaml"
    spec_path.write_text("tasks: []\n")
    harness = _NoMcpRunnableHarness()
    results = run(
        spec=_spec_with_mcp(),
        spec_path=spec_path,
        harness=harness,
        judge=_PassJudge(),
        k=1,
        workers=1,
        timeout=30,
        ablate=["echo"],
    )
    assert harness.seen == {}
    assert results.run.ablated == ["mcp:echo"]
    assert results.run.mcp_servers == []


def test_guard_still_refuses_when_a_server_survives_ablation(tmp_path) -> None:
    spec_path = tmp_path / "m.eval.yaml"
    spec_path.write_text("tasks: []\n")
    spec = EvalSpec(
        mcp={
            "echo": McpServer(command="python3", args=["s.py"]),
            "wiki": McpServer(command="python3", args=["w.py"]),
        },
        tasks=[
            TaskSpec(id="task-001", name="t", prompt="p", assert_script="assert True")
        ],
    )
    with pytest.raises(HarnessConfigurationError, match="does not support MCP yet"):
        run(
            spec=spec,
            spec_path=spec_path,
            harness=_NoMcpHarness(),
            judge=_PassJudge(),
            k=1,
            workers=1,
            timeout=30,
            ablate=["mcp:wiki"],
        )
