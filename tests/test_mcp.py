from __future__ import annotations

import contextlib
import json
import os
import sys
import threading
import time
from pathlib import Path

import psutil
import pytest
from conftest import ScriptedHarness, ScriptedJudge, run_context
from pydantic import ValidationError

from caliper import cancel
from caliper.harness.base import (
    AgentReport,
    CliHarness,
    ConversationTurn,
    HarnessConfigurationError,
    RunContext,
)
from caliper.harness.mcp import (
    McpPreflightInterrupted,
    preflight_stdio_servers,
    resolve_servers,
)
from caliper.runner import RunAborted, run
from caliper.schema.results import Outcome
from caliper.schema.spec import EvalSpec, McpServer, TaskSpec

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


@pytest.mark.parametrize("bad_name", ["wea ther", "we/ather", "wea.ther", "", "good\n"])
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


def _record_server(script: Path) -> Path:
    marker = script.with_suffix(".exchange.json")
    source = script.read_text()
    shebang = ""
    if source.startswith("#!"):
        shebang, source = source.split("\n", 1)
        shebang += "\n"
    script.write_text(
        shebang
        + "import json, os, pathlib, sys\n"
        + f"_exchange_file = pathlib.Path({str(marker)!r})\n"
        + "_exchange = []\n"
        "def _record(direction, message):\n"
        "    _exchange.append([direction, message])\n"
        "    _exchange_file.write_text(json.dumps(_exchange), encoding='utf-8')\n"
        "class _RecordingStream:\n"
        "    def __init__(self, stream, direction):\n"
        "        self.stream, self.direction = stream, direction\n"
        "    def readline(self):\n"
        "        value = self.stream.readline()\n"
        "        if value.strip(): _record(self.direction, json.loads(value))\n"
        "        return value\n"
        "    def __iter__(self): return self\n"
        "    def __next__(self):\n"
        "        value = self.readline()\n"
        "        if not value: raise StopIteration\n"
        "        return value\n"
        "    def write(self, value):\n"
        "        if value.strip(): _record(self.direction, json.loads(value))\n"
        "        return self.stream.write(value)\n"
        "    def flush(self): self.stream.flush()\n"
        "_record('startup', {'host_only_secret': os.getenv('CALIPER_HOST_ONLY_SECRET'), "
        "'path': os.getenv('PATH')})\n"
        "sys.stdin = _RecordingStream(sys.stdin, 'client')\n"
        "sys.stdout = _RecordingStream(sys.stdout, 'server')\n" + source
    )
    return marker


_LEGACY_EXCHANGE = [
    [
        "client",
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "caliper-preflight", "version": "1"},
            },
        },
    ],
    [
        "server",
        {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "protocolVersion": "2025-03-26",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "test", "version": "1"},
            },
        },
    ],
    ["client", {"jsonrpc": "2.0", "method": "notifications/initialized"}],
    ["client", {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}],
    ["server", {"jsonrpc": "2.0", "id": 2, "result": {"tools": []}}],
]


# What a backend that speaks 2026-07-28 sends first, as Claude Code does.
_MODERN_EXCHANGE = [
    [
        "client",
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "server/discover",
            "params": {
                "_meta": {
                    "io.modelcontextprotocol/protocolVersion": "2026-07-28",
                    "io.modelcontextprotocol/clientInfo": {
                        "name": "caliper-preflight",
                        "version": "1",
                    },
                    "io.modelcontextprotocol/clientCapabilities": {},
                }
            },
        },
    ],
    [
        "server",
        {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "supportedVersions": ["2026-07-28"],
                "capabilities": {"tools": {}},
            },
        },
    ],
    [
        "client",
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/list",
            "params": {
                "_meta": {
                    "io.modelcontextprotocol/protocolVersion": "2026-07-28",
                    "io.modelcontextprotocol/clientInfo": {
                        "name": "caliper-preflight",
                        "version": "1",
                    },
                    "io.modelcontextprotocol/clientCapabilities": {},
                }
            },
        },
    ],
    [
        "server",
        {
            "jsonrpc": "2.0",
            "id": 2,
            "result": {
                "resultType": "complete",
                "tools": [],
                "ttlMs": 0,
                "cacheScope": "public",
            },
        },
    ],
]


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
    harness = ScriptedHarness(supports_mcp=True)

    run(spec, spec_path, harness, ScriptedJudge(), k=1, workers=1)

    assert harness.contexts[0].mcp_servers["echo"].command == str(
        spec_dir / "bin/server"
    )
    assert harness.contexts[0].mcp_servers["echo"].args == [
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
    marker = _record_server(script)
    preflight_stdio_servers(
        {"echo": McpServer(command=sys.executable, args=[str(script)])}
    )

    assert json.loads(marker.read_text())[1:] == _LEGACY_EXCHANGE


def test_preflight_accepts_bundled_echo_server_protocol_version(tmp_path) -> None:
    script = Path(__file__).parent / "fixtures" / "mcp" / "echo_server.py"
    wrapper = tmp_path / "bundled.py"
    wrapper.write_text(
        f"import runpy\nrunpy.run_path({str(script)!r}, run_name='__main__')\n"
    )
    marker = _record_server(wrapper)
    preflight_stdio_servers(
        {"echo": McpServer(command=sys.executable, args=[str(wrapper)])}
    )

    assert json.loads(marker.read_text())[1:] == [
        _LEGACY_EXCHANGE[0],
        [
            "server",
            {
                "jsonrpc": "2.0",
                "id": 1,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "echo", "version": "0.1.0"},
                },
            },
        ],
        *_LEGACY_EXCHANGE[2:4],
        [
            "server",
            {
                "jsonrpc": "2.0",
                "id": 2,
                "result": {
                    "tools": [
                        {
                            "name": "secret_word",
                            "description": "Returns the secret word.",
                            "inputSchema": {"type": "object", "properties": {}},
                        }
                    ]
                },
            },
        ],
    ]


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
    marker = _record_server(script)
    preflight_stdio_servers(
        {"echo": McpServer(command=sys.executable, args=[str(script)])}
    )

    assert json.loads(marker.read_text())[1:] == _LEGACY_EXCHANGE


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

    marker = _record_server(script)
    preflight_stdio_servers(
        {"echo": McpServer(command=sys.executable, args=[str(script)])}
    )

    assert json.loads(marker.read_text())[1:] == [
        _LEGACY_EXCHANGE[0],
        [
            "server",
            {
                "jsonrpc": "2.0",
                "method": "notifications/message",
                "params": {
                    "level": "info",
                    "data": "starting",
                },
            },
        ],
        *_LEGACY_EXCHANGE[1:4],
        [
            "server",
            {
                "jsonrpc": "2.0",
                "method": "notifications/message",
                "params": {
                    "level": "info",
                    "data": "ready",
                },
            },
        ],
        _LEGACY_EXCHANGE[4],
    ]


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

    marker = _record_server(script)
    preflight_stdio_servers(
        {"echo": McpServer(command=sys.executable, args=[str(script)])}
    )

    assert json.loads(marker.read_text())[1:] == [
        _LEGACY_EXCHANGE[0],
        ["server", {"jsonrpc": "2.0", "id": 1, "method": "ping"}],
        ["client", {"jsonrpc": "2.0", "id": 1, "result": {}}],
        *_LEGACY_EXCHANGE[1:],
    ]


@pytest.mark.skipif(os.name != "posix", reason="records stdin on SIGTERM")
def test_preflight_accepts_resource_only_server(tmp_path) -> None:
    script = tmp_path / "resources.py"
    # Preflight sends its last message and then terminates the server, so the
    # server records whatever is still unread when SIGTERM lands rather than
    # racing the kill to read it.
    script.write_text(
        "import json, os, signal, sys, threading\n"
        "def _drain(*_):\n"
        "    os.set_blocking(0, False)\n"
        "    try:\n"
        "        pending = os.read(0, 65536)\n"
        "    except BlockingIOError:\n"
        "        pending = b''\n"
        "    for line in pending.splitlines():\n"
        "        _record('client', json.loads(line))\n"
        "    os._exit(0)\n"
        "signal.signal(signal.SIGTERM, _drain)\n"
        "request = json.loads(sys.stdin.readline())\n"
        "print(json.dumps({'jsonrpc': '2.0', 'id': request['id'], "
        f"'result': {_RESOURCE_INITIALIZATION}}}), flush=True)\n"
        "threading.Event().wait()\n"
    )

    marker = _record_server(script)
    preflight_stdio_servers(
        {"resources": McpServer(command=sys.executable, args=[str(script)])}
    )

    assert json.loads(marker.read_text())[1:] == [
        _LEGACY_EXCHANGE[0],
        [
            "server",
            {
                "jsonrpc": "2.0",
                "id": 1,
                "result": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {"resources": {}},
                    "serverInfo": {"name": "test", "version": "1"},
                },
            },
        ],
        _LEGACY_EXCHANGE[2],
    ]


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
            with contextlib.suppress(psutil.NoSuchProcess):
                psutil.Process(pid).kill()


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

    marker = _record_server(command)
    preflight_stdio_servers(
        {"echo": McpServer(command="mcp-server")}, extra_path=[str(tmp_path)]
    )

    assert json.loads(marker.read_text())[1:] == _LEGACY_EXCHANGE
    assert json.loads(marker.read_text())[0][1]["path"].split(os.pathsep)[0] == str(
        tmp_path
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

    marker = _record_server(script)
    preflight_stdio_servers(
        {"echo": McpServer(command=sys.executable, args=[str(script)])}
    )

    assert json.loads(marker.read_text())[1:] == _LEGACY_EXCHANGE
    assert json.loads(marker.read_text())[0][1]["host_only_secret"] is None


class _AttemptPreflightHarness(CliHarness):
    supports_mcp = True
    config_home = ".attempt-preflight"

    @property
    def name(self) -> str:
        return "attempt-preflight"

    def _command(self, ctx: RunContext):
        return (
            [sys.executable, "-c", "print('ok')"],
            None,
            None,
        )

    def _environment(self, ctx: RunContext) -> dict[str, str]:
        return self._isolated_env(ctx)

    def _read(self, proc, ctx) -> AgentReport:
        answer = proc.stdout.strip()
        return AgentReport(
            transcript=[ConversationTurn(role="assistant", content=answer)],
            final_output=answer,
        )


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


_NOT_FOUND = {"error": {"code": -32601, "message": "Method not found"}}
_DISCOVERED_TOOLS = {
    "result": {"supportedVersions": ["2026-07-28"], "capabilities": {"tools": {}}}
}
_MODERN_TOOLS = {
    "result": {
        "resultType": "complete",
        "tools": [],
        "ttlMs": 0,
        "cacheScope": "public",
    }
}


def _modern_server(
    tmp_path, discover_reply: dict, tools_reply: dict = _MODERN_TOOLS
) -> dict[str, McpServer]:
    """A server that rejects initialize and answers 2026-07-28 requests.

    It exits instead of answering a modern request that lacks the 2026-07-28
    ``_meta``, so a passing check proves preflight sent it.
    """
    script = tmp_path / "modern.py"
    script.write_text(
        "import json, sys\n"
        "for line in sys.stdin:\n"
        "    request = json.loads(line)\n"
        "    if 'id' not in request: continue\n"
        "    if request['method'] == 'initialize':\n"
        f"        body = {_NOT_FOUND!r}\n"
        "    elif request['params']['_meta']"
        "['io.modelcontextprotocol/protocolVersion'] != '2026-07-28':\n"
        "        sys.exit(2)\n"
        "    elif request['method'] == 'server/discover':\n"
        f"        body = {discover_reply!r}\n"
        "    else:\n"
        f"        body = {tools_reply!r}\n"
        "    print(json.dumps({'jsonrpc': '2.0', 'id': request['id'], **body}), "
        "flush=True)\n"
    )
    return {"modern": McpServer(command=sys.executable, args=[str(script)])}


def test_preflight_accepts_a_modern_only_server_when_the_agent_speaks_it(
    tmp_path,
) -> None:
    servers = _modern_server(tmp_path, _DISCOVERED_TOOLS)
    marker = _record_server(tmp_path / "modern.py")
    preflight_stdio_servers(servers, speaks_modern=True)

    assert json.loads(marker.read_text())[1:] == _MODERN_EXCHANGE


def test_preflight_rejects_a_modern_only_server_when_the_agent_cannot_speak_it(
    tmp_path,
) -> None:
    with pytest.raises(
        HarnessConfigurationError,
        match=r"^MCP server 'modern' speaks only MCP 2026-07-28, which this "
        r"backend's agent cannot connect to\. Use a server that also answers "
        r"initialize, or a backend that speaks 2026-07-28\.$",
    ):
        preflight_stdio_servers(_modern_server(tmp_path, _DISCOVERED_TOOLS))


def test_preflight_rejects_a_modern_server_sharing_no_version(tmp_path) -> None:
    unsupported = {
        "error": {
            "code": -32022,
            "message": "Unsupported protocol version",
            "data": {"supported": ["2027-01-01"], "requested": "2026-07-28"},
        }
    }

    with pytest.raises(
        HarnessConfigurationError,
        match=r"^MCP server 'modern' does not support MCP 2026-07-28 "
        r"\(it supports 2027-01-01\)$",
    ):
        preflight_stdio_servers(
            _modern_server(tmp_path, unsupported), speaks_modern=True
        )


def test_preflight_rejects_a_server_that_rejects_initialize_and_discovery(
    tmp_path,
) -> None:
    with pytest.raises(
        HarnessConfigurationError, match=r"^MCP server 'modern' rejected initialize$"
    ):
        preflight_stdio_servers(
            _modern_server(tmp_path, _NOT_FOUND), speaks_modern=True
        )


def test_preflight_rejects_a_modern_server_that_does_not_list_its_tools(
    tmp_path,
) -> None:
    with pytest.raises(
        HarnessConfigurationError, match=r"^MCP server 'modern' did not list tools$"
    ):
        preflight_stdio_servers(
            _modern_server(tmp_path, _DISCOVERED_TOOLS, tools_reply=_NOT_FOUND),
            speaks_modern=True,
        )


# Each one made Claude Code 2.1.306 drop the server's tools.
_DROPPED_TOOL_LISTS = [
    {"resultType": "partial"},
    {"ttlMs": 1.5},
    {"ttlMs": -1},
    {"ttlMs": 2**60},
    {"cacheScope": "shared"},
]


@pytest.mark.parametrize("fields", _DROPPED_TOOL_LISTS)
def test_preflight_rejects_a_modern_tool_list_claude_code_would_drop(
    tmp_path, fields
) -> None:
    tools = {**_MODERN_TOOLS["result"], **fields}
    with pytest.raises(
        HarnessConfigurationError,
        match=r"^MCP server 'modern' listed tools with an invalid resultType, "
        r"ttlMs or cacheScope$",
    ):
        preflight_stdio_servers(
            _modern_server(tmp_path, _DISCOVERED_TOOLS, tools_reply={"result": tools}),
            speaks_modern=True,
        )


@pytest.mark.parametrize("missing", ["resultType", "ttlMs", "cacheScope"])
def test_preflight_accepts_a_modern_tool_list_that_leaves_a_field_out(
    tmp_path, missing
) -> None:
    """Claude Code 2.1.306 still lists and calls the tools then."""
    tools = {k: v for k, v in _MODERN_TOOLS["result"].items() if k != missing}
    preflight_stdio_servers(
        _modern_server(tmp_path, _DISCOVERED_TOOLS, tools_reply={"result": tools}),
        speaks_modern=True,
    )


def test_preflight_accepts_a_whole_ttl_written_as_a_float(tmp_path) -> None:
    tools = {"result": {**_MODERN_TOOLS["result"], "ttlMs": 1.0}}

    servers = _modern_server(tmp_path, _DISCOVERED_TOOLS, tools_reply=tools)
    marker = _record_server(tmp_path / "modern.py")
    preflight_stdio_servers(servers, speaks_modern=True)

    assert json.loads(marker.read_text())[1:] == [
        *_MODERN_EXCHANGE[:-1],
        [
            "server",
            {
                "jsonrpc": "2.0",
                "id": 2,
                "result": {
                    "resultType": "complete",
                    "tools": [],
                    "ttlMs": 1.0,
                    "cacheScope": "public",
                },
            },
        ],
    ]


@pytest.mark.parametrize(
    ("discover_reply", "tools_reply", "error"),
    [
        ({**_DISCOVERED_TOOLS, "error": None}, _MODERN_TOOLS, "rejected initialize"),
        (_DISCOVERED_TOOLS, {**_MODERN_TOOLS, "error": None}, "did not list tools"),
    ],
)
def test_preflight_rejects_a_modern_reply_with_a_null_error(
    tmp_path, discover_reply, tools_reply, error
) -> None:
    with pytest.raises(
        HarnessConfigurationError, match=rf"^MCP server 'modern' {error}$"
    ):
        preflight_stdio_servers(
            _modern_server(tmp_path, discover_reply, tools_reply),
            speaks_modern=True,
        )


def test_preflight_rejects_a_discovery_without_2026_07_28(tmp_path) -> None:
    discovered = {
        "result": {"supportedVersions": ["2027-01-01"], "capabilities": {"tools": {}}}
    }

    with pytest.raises(
        HarnessConfigurationError,
        match=r"^MCP server 'modern' does not support MCP 2026-07-28 "
        r"\(it supports 2027-01-01\)$",
    ):
        preflight_stdio_servers(
            _modern_server(tmp_path, discovered), speaks_modern=True
        )


def test_preflight_names_no_versions_when_the_server_lists_none(tmp_path) -> None:
    unsupported = {"error": {"code": -32022, "message": "Unsupported"}}

    with pytest.raises(
        HarnessConfigurationError,
        match=r"^MCP server 'modern' does not support MCP 2026-07-28$",
    ):
        preflight_stdio_servers(
            _modern_server(tmp_path, unsupported), speaks_modern=True
        )


def test_preflight_treats_a_null_error_beside_a_result_as_legacy(tmp_path) -> None:
    script = tmp_path / "server.py"
    script.write_text(
        "import json, sys\n"
        "request = json.loads(sys.stdin.readline())\n"
        "print(json.dumps({'jsonrpc': '2.0', 'id': request['id'], 'error': None, "
        f"'result': {_TOOL_INITIALIZATION}}}), flush=True)\n" + _TOOLS_REPLY
    )

    marker = _record_server(script)
    preflight_stdio_servers(
        {"echo": McpServer(command=sys.executable, args=[str(script)])}
    )

    assert json.loads(marker.read_text())[1:] == [
        _LEGACY_EXCHANGE[0],
        ["server", {**_LEGACY_EXCHANGE[1][1], "error": None}],
        *_LEGACY_EXCHANGE[2:],
    ]


def test_preflight_reports_a_garbled_discovery_as_rejected_initialize(
    tmp_path,
) -> None:
    script = tmp_path / "server.py"
    script.write_text(
        "import json, sys\n"
        "request = json.loads(sys.stdin.readline())\n"
        "print(json.dumps({'jsonrpc': '2.0', 'id': request['id'], "
        f"**{_NOT_FOUND!r}}}), flush=True)\n"
        "sys.stdin.readline()\n"
        "print('not json', flush=True)\n"
        "sys.stdin.readline()\n"
    )

    with pytest.raises(
        HarnessConfigurationError, match=r"^MCP server 'garbled' rejected initialize$"
    ):
        preflight_stdio_servers(
            {"garbled": McpServer(command=sys.executable, args=[str(script)])}
        )


class _ModernAttemptPreflightHarness(_AttemptPreflightHarness):
    speaks_modern_mcp = True


@pytest.mark.parametrize(
    ("harness", "error"),
    [
        (_ModernAttemptPreflightHarness, None),
        (_AttemptPreflightHarness, "speaks only MCP 2026-07-28"),
    ],
)
def test_attempt_preflight_follows_the_backend_modern_declaration(
    tmp_path, harness, error
) -> None:
    home = tmp_path / "home"
    workdir = home / "work"
    workdir.mkdir(parents=True)
    ctx = run_context(
        isolated_home=str(home),
        workdir=str(workdir),
        mcp_servers=_modern_server(tmp_path, _DISCOVERED_TOOLS),
    )

    if error is None:
        assert harness().run(ctx).final_output == "ok"
    else:
        with pytest.raises(HarnessConfigurationError, match=error):
            harness().run(ctx)


def test_preflight_fails_fast_when_a_rejecting_server_ignores_discovery(
    tmp_path,
) -> None:
    script = tmp_path / "silent.py"
    script.write_text(
        "import json, sys, time\n"
        "request = json.loads(sys.stdin.readline())\n"
        "print(json.dumps({'jsonrpc': '2.0', 'id': request['id'], "
        f"**{_NOT_FOUND!r}}}), flush=True)\n"
        "time.sleep(60)\n"
    )
    start = time.monotonic()

    with pytest.raises(
        HarnessConfigurationError, match=r"^MCP server 'silent' rejected initialize$"
    ):
        preflight_stdio_servers(
            {"silent": McpServer(command=sys.executable, args=[str(script)])},
            timeout=60,
        )

    assert time.monotonic() - start < 8


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

    results = run(spec, spec_path, _AttemptPreflightHarness(), ScriptedJudge(), k=1)

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
        assert errors
        assert isinstance(errors[0], McpPreflightInterrupted)
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
                spec, spec_path, _AttemptPreflightHarness(), ScriptedJudge(), k=1
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
    harness = ScriptedHarness(supports_mcp=True)

    run(spec, spec_path, harness, ScriptedJudge(), k=1, workers=1)

    assert harness.contexts[0].mcp_servers["weather"].args == [str(script)]


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
            ScriptedJudge(),
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
        run(
            spec, spec_path, _AttemptPreflightHarness(), ScriptedJudge(), k=1, workers=1
        )
    assert exc.value.results.task_results[0].attempts == []


# --- run-seam capability guard --------------------------------------------


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
    harness = ScriptedHarness()
    with pytest.raises(
        HarnessConfigurationError, match="does not support MCP yet"
    ) as exc:
        run(
            spec=_spec_with_mcp(),
            spec_path=spec_path,
            harness=harness,
            judge=ScriptedJudge(),
            k=1,
            workers=1,
            timeout=30,
        )
    assert "in this release" in str(exc.value)
    assert harness.calls == 0


def test_guard_refusal_uses_backend_hint_when_present(tmp_path) -> None:
    spec_path = tmp_path / "m.eval.yaml"
    spec_path.write_text("tasks: []\n")
    # A backend whose lack of MCP is permanent-by-design supplies its own hint,
    # which the refusal carries verbatim — and drops the misleading "yet".
    harness = ScriptedHarness(
        mcp_unsupported_hint="Expose it as a CLI tool the skill drives instead."
    )
    with pytest.raises(HarnessConfigurationError) as exc:
        run(
            spec=_spec_with_mcp(),
            spec_path=spec_path,
            harness=harness,
            judge=ScriptedJudge(),
            k=1,
            workers=1,
            timeout=30,
        )
    message = str(exc.value)
    assert "Expose it as a CLI tool the skill drives instead." in message
    assert "does not support MCP yet" not in message
    assert harness.calls == 0


def test_guard_allows_mcp_spec_on_supporting_backend(tmp_path) -> None:
    spec_path = tmp_path / "m.eval.yaml"
    spec_path.write_text("tasks: []\n")
    harness = ScriptedHarness(supports_mcp=True)
    run(
        spec=_spec_with_mcp(),
        spec_path=spec_path,
        harness=harness,
        judge=ScriptedJudge(),
        k=1,
        workers=1,
        timeout=30,
    )
    # The runner threads the declared McpServer models straight to the backend.
    assert harness.contexts[0].mcp_servers == {
        "echo": McpServer(command="python3", args=["s.py"])
    }


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
    harness = ScriptedHarness(supports_mcp=True)
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
        judge=ScriptedJudge(),
        k=1,
        workers=1,
        timeout=30,
    )
    assert harness.contexts[0].mcp_servers == {}


def test_an_omitted_mcp_block_leaves_the_backend_config_alone(tmp_path) -> None:
    spec_path = tmp_path / "m.eval.yaml"
    spec_path.write_text("tasks: []\n")
    harness = ScriptedHarness(supports_mcp=True)
    run(
        spec=_spec_without_mcp(),
        spec_path=spec_path,
        harness=harness,
        judge=ScriptedJudge(),
        k=1,
        workers=1,
        timeout=30,
    )
    assert harness.contexts[0].mcp_servers is None


def test_ablating_every_server_runs_on_a_backend_without_mcp(tmp_path) -> None:
    # The guard exists so declared tools are not silently absent. With every
    # server ablated the absence is the caller's explicit choice, recorded in
    # RunMeta.ablated, so the spec is runnable on a backend that cannot honor it.
    # The backend still gets an empty mapping, not None: the block was declared,
    # so a supporting backend must isolate to zero servers rather than fall back
    # to its ambient config.
    spec_path = tmp_path / "m.eval.yaml"
    spec_path.write_text("tasks: []\n")
    harness = ScriptedHarness()
    results = run(
        spec=_spec_with_mcp(),
        spec_path=spec_path,
        harness=harness,
        judge=ScriptedJudge(),
        k=1,
        workers=1,
        timeout=30,
        ablate=["echo"],
    )
    assert harness.contexts[0].mcp_servers == {}
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
            harness=ScriptedHarness(),
            judge=ScriptedJudge(),
            k=1,
            workers=1,
            timeout=30,
            ablate=["mcp:wiki"],
        )


_LEGACY_INIT = {
    "result": {
        "protocolVersion": "2025-03-26",
        "capabilities": {"tools": {}},
        "serverInfo": {"name": "test", "version": "1"},
    }
}
_A_TOOL = {"name": "echo", "inputSchema": {"type": "object"}}
_A_TOOL_REPLY = ["server", {"jsonrpc": "2.0", "id": 2, "result": {"tools": [_A_TOOL]}}]


def _legacy_server(
    tmp_path, init_reply: dict, tools_reply: dict | None = None
) -> dict[str, McpServer]:
    """A server answering initialize, then tools/list, with these envelopes.

    ``jsonrpc`` and ``id`` are filled in unless the envelope sets them.
    """
    tools_reply = tools_reply or {"result": {"tools": [_A_TOOL]}}
    script = tmp_path / "legacy.py"
    script.write_text(
        "import json, sys\n"
        "def answer(body):\n"
        "    request = json.loads(sys.stdin.readline())\n"
        "    print(json.dumps({'jsonrpc': '2.0', 'id': request['id'], **body}), "
        "flush=True)\n"
        f"answer({init_reply!r})\n"
        "sys.stdin.readline()\n"  # notifications/initialized
        f"answer({tools_reply!r})\n"
        "sys.stdin.readline()\n"
    )
    return {"legacy": McpServer(command=sys.executable, args=[str(script)])}


def test_preflight_accepts_a_well_formed_tool_list(tmp_path) -> None:
    servers = _legacy_server(tmp_path, _LEGACY_INIT)
    marker = _record_server(tmp_path / "legacy.py")
    preflight_stdio_servers(servers)

    assert json.loads(marker.read_text())[1:] == [
        *_LEGACY_EXCHANGE[:4],
        _A_TOOL_REPLY,
    ]


@pytest.mark.parametrize(
    "init_reply",
    [
        {**_LEGACY_INIT, "jsonrpc": "1.0"},
        {**_LEGACY_INIT, "error": {"code": -32603, "message": "boom"}},
    ],
    ids=["wrong-jsonrpc-version", "result-and-error"],
)
def test_preflight_rejects_a_malformed_response_envelope(tmp_path, init_reply) -> None:
    with pytest.raises(
        HarnessConfigurationError, match=r"^MCP server 'legacy' sent an invalid"
    ):
        preflight_stdio_servers(_legacy_server(tmp_path, init_reply))


def test_preflight_tolerates_a_null_error_beside_a_legacy_result(tmp_path) -> None:
    """codex accepts ``error: null`` on the legacy path, so preflight does too."""
    servers = _legacy_server(tmp_path, {**_LEGACY_INIT, "error": None})
    marker = _record_server(tmp_path / "legacy.py")
    preflight_stdio_servers(servers)

    assert json.loads(marker.read_text())[1:] == [
        _LEGACY_EXCHANGE[0],
        ["server", {**_LEGACY_EXCHANGE[1][1], "error": None}],
        *_LEGACY_EXCHANGE[2:4],
        _A_TOOL_REPLY,
    ]


@pytest.mark.parametrize(
    "tools",
    [[42], [{"name": "echo"}], [{"inputSchema": {}}], [{"name": 1, "inputSchema": {}}]],
    ids=["not-an-object", "no-schema", "no-name", "non-string-name"],
)
def test_preflight_rejects_a_tool_the_agent_could_not_use(tmp_path, tools) -> None:
    with pytest.raises(
        HarnessConfigurationError,
        match=r"^MCP server 'legacy' listed a tool without a name and inputSchema",
    ):
        preflight_stdio_servers(
            _legacy_server(tmp_path, _LEGACY_INIT, {"result": {"tools": tools}})
        )


def _dual_era_server(
    tmp_path, tools_reply: dict = _MODERN_TOOLS
) -> dict[str, McpServer]:
    """A server answering both revisions by method, as a dual-era server does.

    A ``tools/list`` without the 2026-07-28 ``_meta`` gets a legacy tool list.
    """
    script = tmp_path / "dual.py"
    script.write_text(
        "import json, sys\n"
        "for line in sys.stdin:\n"
        "    request = json.loads(line)\n"
        "    if 'id' not in request: continue\n"
        "    if request['method'] == 'initialize':\n"
        f"        body = {_LEGACY_INIT!r}\n"
        "    elif request['method'] == 'server/discover':\n"
        f"        body = {_DISCOVERED_TOOLS!r}\n"
        "    elif 'params' in request:\n"
        f"        body = {tools_reply!r}\n"
        "    else:\n"
        "        body = {'result': {'tools': []}}\n"
        "    print(json.dumps({'jsonrpc': '2.0', 'id': request['id'], **body}), "
        "flush=True)\n"
    )
    return {"dual": McpServer(command=sys.executable, args=[str(script)])}


def test_preflight_checks_a_dual_era_server_in_2026_07_28_when_the_agent_speaks_it(
    tmp_path,
) -> None:
    """Claude Code stays on 2026-07-28 with such a server, so its tool list
    there is the one that has to be valid."""
    with pytest.raises(
        HarnessConfigurationError,
        match=r"^MCP server 'dual' listed tools with an invalid resultType",
    ):
        preflight_stdio_servers(
            _dual_era_server(
                tmp_path, {"result": {**_MODERN_TOOLS["result"], "ttlMs": 1.5}}
            ),
            speaks_modern=True,
        )


@pytest.mark.parametrize(
    ("speaks_modern", "exchange"),
    [(True, _MODERN_EXCHANGE), (False, _LEGACY_EXCHANGE)],
    ids=["modern-backend", "legacy-backend"],
)
def test_preflight_opens_a_dual_era_server_as_the_backend_agent_does(
    tmp_path, speaks_modern, exchange
) -> None:
    servers = _dual_era_server(tmp_path)
    marker = _record_server(tmp_path / "dual.py")
    preflight_stdio_servers(servers, speaks_modern=speaks_modern)

    assert json.loads(marker.read_text())[1:] == exchange


def _initialize_only_server(tmp_path, on_discover: str) -> dict[str, McpServer]:
    """A legacy server that answers ``server/discover`` with an error, by
    exiting, or not at all."""
    reaction = {
        "error": f"        body = {_NOT_FOUND!r}\n",
        "exit": "        sys.exit(1)\n",
        "silent": "        continue\n",
    }[on_discover]
    script = tmp_path / "legacy.py"
    script.write_text(
        "import json, sys\n"
        "for line in sys.stdin:\n"
        "    request = json.loads(line)\n"
        "    if 'id' not in request: continue\n"
        "    if request['method'] == 'initialize':\n"
        f"        body = {_LEGACY_INIT!r}\n"
        "    elif request['method'] == 'tools/list':\n"
        "        body = {'result': {'tools': []}}\n"
        "    else:\n" + reaction + "    print(json.dumps({'jsonrpc': '2.0', "
        "'id': request['id'], **body}), flush=True)\n"
    )
    return {"legacy": McpServer(command=sys.executable, args=[str(script)])}


def test_preflight_falls_back_to_initialize_when_discovery_is_rejected(
    tmp_path,
) -> None:
    servers = _initialize_only_server(tmp_path, "error")
    marker = _record_server(tmp_path / "legacy.py")
    preflight_stdio_servers(servers, speaks_modern=True)

    initialize, init_reply, initialized, tools, tools_reply = _LEGACY_EXCHANGE
    assert json.loads(marker.read_text())[1:] == [
        _MODERN_EXCHANGE[0],
        ["server", {"jsonrpc": "2.0", "id": 1, **_NOT_FOUND}],
        ["client", {**initialize[1], "id": 2}],
        ["server", {**init_reply[1], "id": 2}],
        initialized,
        ["client", {**tools[1], "id": 3}],
        ["server", {**tools_reply[1], "id": 3}],
    ]


@pytest.mark.parametrize("on_discover", ["exit", "silent"])
def test_preflight_restarts_a_legacy_server_that_does_not_answer_discovery(
    tmp_path, on_discover
) -> None:
    servers = _initialize_only_server(tmp_path, on_discover)
    marker = _record_server(tmp_path / "legacy.py")
    start = time.monotonic()
    preflight_stdio_servers(servers, speaks_modern=True)

    # The marker holds the fresh process's exchange.
    assert json.loads(marker.read_text())[1:] == _LEGACY_EXCHANGE
    assert time.monotonic() - start < 8
