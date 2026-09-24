"""Shared MCP resolution used by every backend that materializes ``mcp:`` servers.

This module owns the two rules every MCP-capable backend must agree on:

- **The secret rule** (docs/adr/0009-mcp-secrets-interpolated-at-the-harness-boundary.md):
  a declared server's secrets are referenced by host environment variable as
  ``${VAR}`` and resolved at the harness boundary (never written into the
  committed spec), so an unset var is a configuration error surfaced here
  rather than an opaque connect-time failure.
- **The shape rule**: which fields a resolved server carries per transport —
  ``url``/``headers`` for a remote server, ``command``/``args``/``env`` for a
  stdio one — and that ``${VAR}`` is honored in ``env`` values, ``headers``
  values, and a remote ``url``, never in ``command``/``args``.

``resolve_servers`` applies both rules once; a backend is left with only its
config-key spelling (e.g. codex renames ``headers`` to ``http_headers``, see
docs/adr/0011-codex-remote-mcp-uses-static-http-headers-not-env-indirection.md).
"""

from __future__ import annotations

import json
import os
import queue
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from caliper import cancel
from caliper.harness.base import HarnessConfigurationError
from caliper.schema.spec import McpServer

# A ``${VAR}`` reference inside an MCP server field (stdio ``env`` values, remote
# ``headers`` values, a remote ``url``). Only this exact form is honored.
ENV_VAR_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
_PREFLIGHT_TIMEOUT = 15.0


def resolve_declared_paths(
    declared: dict[str, McpServer], spec_dir: Path
) -> dict[str, McpServer]:
    """Anchor explicitly relative stdio paths to the spec, not the attempt cwd."""

    def anchored(value: str) -> str:
        if value.startswith(("./", "../")):
            return str((spec_dir / value).resolve())
        return value

    return {
        name: (
            server.model_copy(
                update={
                    "command": anchored(server.command),
                    "args": [anchored(arg) for arg in server.args],
                }
            )
            if not server.is_remote
            else server
        )
        for name, server in declared.items()
    }


def preflight_stdio_servers(
    declared: dict[str, McpServer],
    *,
    extra_path: list[str] | None = None,
    env: dict[str, str] | None = None,
    cwd: str | None = None,
) -> None:
    """Verify each local server in its prepared attempt before the agent starts.

    A successful process spawn is insufficient: a dead script can exit at once,
    or a process can stay alive without speaking MCP. Bound the MCP exchange so
    neither failure becomes a scored attempt or a hanging agent.
    """
    for name, server in resolve_servers(declared).items():
        if cancel.requested():
            raise HarnessConfigurationError("MCP preflight interrupted")
        if server.is_remote:
            continue
        initialize = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "caliper-preflight", "version": "1"},
            },
        }
        try:
            with tempfile.TemporaryDirectory(
                prefix="caliper-mcp-preflight-"
            ) as tmp_dir:
                # A run-level check has no attempt yet. Keep its environment as
                # narrow as an attempt's, so a server cannot pass preflight by
                # relying on a host variable the agent will never inherit.
                process_env = (
                    dict(env)
                    if env is not None
                    else {
                        "HOME": tmp_dir,
                        "PATH": os.pathsep.join(
                            [*(extra_path or []), os.environ.get("PATH", "")]
                        ),
                    }
                )
                if (
                    env is None
                    and sys.platform == "win32"
                    and "SystemRoot" in os.environ
                ):
                    process_env["SystemRoot"] = os.environ["SystemRoot"]
                process_env.update(server.env)
                process = subprocess.Popen(
                    [server.command, *server.args],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    text=True,
                    encoding="utf-8",
                    cwd=cwd or tmp_dir,
                    env=process_env,
                    start_new_session=os.name == "posix",
                )
                try:
                    with cancel.track(process):
                        response = _exchange(process, initialize, name)
                        result = response.get("result")
                        if not isinstance(result, dict):
                            raise HarnessConfigurationError(
                                f"MCP server '{name}' rejected initialization"
                            )
                        process.stdin.write(
                            json.dumps(
                                {
                                    "jsonrpc": "2.0",
                                    "method": "notifications/initialized",
                                }
                            )
                            + "\n"
                        )
                        process.stdin.flush()
                        capabilities = result.get("capabilities")
                        if isinstance(capabilities, dict) and "tools" in capabilities:
                            response = _exchange(
                                process,
                                {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                                name,
                            )
                            tools_result = response.get("result")
                            if not isinstance(tools_result, dict) or not isinstance(
                                tools_result.get("tools"), list
                            ):
                                raise HarnessConfigurationError(
                                    f"MCP server '{name}' did not list tools"
                                )
                        try:
                            process.wait(timeout=0.05)
                        except subprocess.TimeoutExpired:
                            pass
                        else:
                            raise HarnessConfigurationError(
                                f"MCP server '{name}' exited after initialization"
                            )
                finally:
                    # Launchers such as npx can leave a child holding the stdio
                    # pipe after their parent exits. End the whole preflight
                    # group before the real attempt starts.
                    if os.name == "posix":
                        try:
                            os.killpg(process.pid, signal.SIGTERM)
                        except ProcessLookupError:
                            pass
                    else:
                        if process.poll() is None:
                            process.terminate()
                    try:
                        process.wait(timeout=1)
                    except subprocess.TimeoutExpired:
                        pass
                    # The launcher can exit before a child in its group does.
                    # Kill the group even when wait() already reaped the leader.
                    if os.name == "posix":
                        try:
                            os.killpg(process.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                    elif process.poll() is None:
                        process.kill()
                    process.wait()
        except (OSError, ValueError) as exc:
            raise HarnessConfigurationError(
                f"MCP server '{name}' failed to start: {exc}"
            ) from exc


def _exchange(process: subprocess.Popen, request: dict, name: str) -> dict:
    """Read the matching MCP response, ignoring intervening notifications."""
    process.stdin.write(json.dumps(request) + "\n")
    process.stdin.flush()
    deadline = time.monotonic() + _PREFLIGHT_TIMEOUT
    while True:
        replies: queue.Queue[str] = queue.Queue(maxsize=1)
        reader = threading.Thread(
            target=lambda: replies.put(process.stdout.readline()), daemon=True
        )
        reader.start()
        while True:
            if cancel.requested():
                raise HarnessConfigurationError("MCP preflight interrupted")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise HarnessConfigurationError(
                    f"MCP server '{name}' did not answer {request['method']} within "
                    f"{_PREFLIGHT_TIMEOUT:g} seconds"
                )
            try:
                line = replies.get(timeout=min(0.05, remaining))
                break
            except queue.Empty:
                continue
        if not line:
            raise HarnessConfigurationError(
                f"MCP server '{name}' exited before answering {request['method']}"
            )
        response = json.loads(line)
        if not isinstance(response, dict):
            raise HarnessConfigurationError(
                f"MCP server '{name}' sent an invalid response"
            )
        if response.get("id") == request["id"]:
            if "result" not in response:
                raise HarnessConfigurationError(
                    f"MCP server '{name}' rejected {request['method']}"
                )
            return response
        if "method" in response and "id" in response:
            # We advertise no client capabilities, so a server-initiated
            # request cannot be served. Reply rather than leaving it waiting.
            process.stdin.write(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": response["id"],
                        "error": {"code": -32601, "message": "Method not found"},
                    }
                )
                + "\n"
            )
            process.stdin.flush()


def interpolate(value: str, *, server_name: str, field_label: str) -> str:
    """Resolve ``${VAR}`` references in ``value`` from the parent ``os.environ``.

    ``server_name``/``field_label`` name the spec location for the error message
    when a referenced var is unset. This is the single point where a secret
    enters a run.
    """

    def replace(match: re.Match[str]) -> str:
        var = match.group(1)
        if var not in os.environ:
            raise HarnessConfigurationError(
                f"MCP server '{server_name}' needs env var {var} (referenced "
                f"by {field_label}), but it is not set.\n\n"
                f"export {var}=... and rerun caliper."
            )
        return os.environ[var]

    return ENV_VAR_RE.sub(replace, value)


@dataclass
class ResolvedMcpServer:
    """A declared server with every ``${VAR}`` already resolved to its literal.

    Values here may hold real secrets: they must only ever be written into a
    run-scoped config file (kept ``0600``), never into argv or the child env.
    """

    type: str
    is_remote: bool
    url: str | None = None
    headers: dict[str, str] = field(default_factory=dict)
    command: str | None = None
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)

    def entry(self) -> dict:
        """The common config rendering every backend starts from.

        A remote server as ``{url, headers?}``, a stdio server as
        ``{command, args?, env?}`` — empty optionals omitted. A backend whose
        CLI spells a key differently renames it on this dict.
        """
        if self.is_remote:
            rendered: dict = {"url": self.url}
            if self.headers:
                rendered["headers"] = self.headers
            return rendered
        rendered = {"command": self.command}
        if self.args:
            rendered["args"] = self.args
        if self.env:
            rendered["env"] = self.env
        return rendered


def resolve_servers(
    declared: dict[str, McpServer] | None,
) -> dict[str, ResolvedMcpServer]:
    """Resolve the declared ``mcp:`` servers into interpolated field values.

    Walks the declared mapping, branches on transport, and interpolates
    ``${VAR}`` in a remote ``url``/``headers`` and a stdio ``env`` — the one
    walk every backend used to hand-roll. An unset var raises
    ``HarnessConfigurationError`` here, at the boundary.
    """
    resolved: dict[str, ResolvedMcpServer] = {}
    for name, server in (declared or {}).items():
        if server.is_remote:
            resolved[name] = ResolvedMcpServer(
                type=server.type,
                is_remote=True,
                url=interpolate(server.url, server_name=name, field_label="url"),
                headers={
                    key: interpolate(
                        value, server_name=name, field_label=f"headers.{key}"
                    )
                    for key, value in server.headers.items()
                },
            )
        else:
            resolved[name] = ResolvedMcpServer(
                type=server.type,
                is_remote=False,
                command=server.command,
                args=list(server.args),
                env={
                    key: interpolate(value, server_name=name, field_label=f"env.{key}")
                    for key, value in server.env.items()
                },
            )
    return resolved
