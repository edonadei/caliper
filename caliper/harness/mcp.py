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

import contextlib
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

import psutil

from caliper import cancel
from caliper.harness.base import HarnessConfigurationError, RunContext
from caliper.schema.spec import McpServer

# A ``${VAR}`` reference inside an MCP server field (stdio ``env`` values, remote
# ``headers`` values, a remote ``url``). Only this exact form is honored.
ENV_VAR_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
_PREFLIGHT_TIMEOUT = 15.0
# A server may answer ``initialize`` with any revision it supports rather than
# the one proposed; the agent's own MCP client does the real negotiation.
_KNOWN_PROTOCOL_VERSIONS = frozenset(
    {"2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25"}
)
# 2026-07-28 drops the initialize handshake: every request carries the version
# and client identity in ``params._meta`` instead (docs/adr/0035).
_MODERN_PROTOCOL_VERSION = "2026-07-28"
_MODERN_META = {
    "io.modelcontextprotocol/protocolVersion": _MODERN_PROTOCOL_VERSION,
    "io.modelcontextprotocol/clientInfo": {"name": "caliper-preflight", "version": "1"},
    "io.modelcontextprotocol/clientCapabilities": {},
}
_UNSUPPORTED_PROTOCOL_VERSION = -32022
# Claude Code reads ``ttlMs`` as a JavaScript number, so 1.0 is a valid integer.
_MAX_SAFE_INTEGER = 2**53 - 1
# Claude Code gives up on ``server/discover`` after about 3 seconds.
_DISCOVER_TIMEOUT = 3.0


class McpPreflightInterrupted(Exception):
    """Cooperative cancellation while checking a declared MCP server."""


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
    timeout: float = _PREFLIGHT_TIMEOUT,
    speaks_modern: bool = False,
) -> None:
    """Verify each local server in its prepared attempt before the agent starts.

    A successful process spawn is insufficient: a dead script can exit at once,
    or a process can stay alive without speaking MCP. Bound the MCP exchange so
    neither failure becomes a scored attempt or a hanging agent. ``timeout``
    bounds the whole check across every server; an attempt passes its
    ``--timeout`` so a server that starts slowly gets the same budget the agent
    would. A server that speaks only MCP 2026-07-28 passes only when
    ``speaks_modern`` says the agent's own client can connect to it.
    """
    if os.name == "nt":
        from caliper.harness import windows_job
    else:
        windows_job = None
    deadline = time.monotonic() + timeout
    for name, server in resolve_servers(declared).items():
        if cancel.requested():
            raise McpPreflightInterrupted
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
                # Keep preflight as narrow as the attempt's environment, so a
                # server cannot pass by relying on an ambient host variable.
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
                    process_env["SystemRoot"] = os.environ["SystemRoot"]  # noqa: SIM112
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
                    creationflags=(
                        windows_job.CREATE_SUSPENDED if windows_job is not None else 0
                    ),
                )
                job = None
                try:
                    if windows_job is not None:
                        job = windows_job.assign_and_resume(process)
                    with cancel.track(process):
                        response = _exchange(
                            process, initialize, name, deadline, timeout
                        )
                        if "result" not in response:
                            _discover(
                                process,
                                name,
                                deadline,
                                timeout,
                                speaks_modern=speaks_modern,
                            )
                            stage = "discovery"
                        else:
                            _initialized(
                                process, response.get("result"), name, deadline, timeout
                            )
                            stage = "initialization"
                        try:
                            process.wait(timeout=0.05)
                        except subprocess.TimeoutExpired:
                            pass
                        else:
                            raise HarnessConfigurationError(
                                f"MCP server '{name}' exited after {stage}"
                            )
                finally:
                    if os.name == "posix":
                        # Capture children before terminating the launcher;
                        # reparenting would otherwise hide them from the walk.
                        descendants: set[psutil.Process] = set()
                        with contextlib.suppress(psutil.Error):
                            descendants.update(
                                psutil.Process(process.pid).children(recursive=True)
                            )
                        with contextlib.suppress(OSError):
                            os.killpg(process.pid, signal.SIGTERM)
                        with contextlib.suppress(subprocess.TimeoutExpired):
                            process.wait(timeout=1)
                        # The launcher can exit before another group member.
                        with contextlib.suppress(OSError):
                            os.killpg(process.pid, signal.SIGKILL)
                        for child in descendants:
                            with contextlib.suppress(
                                psutil.NoSuchProcess, psutil.AccessDenied
                            ):
                                child.kill()
                        psutil.wait_procs(list(descendants), timeout=1)
                    elif job is not None:
                        windows_job.close(job)
                    elif process.poll() is None:
                        process.kill()
                    try:
                        process.wait(timeout=1)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=1)
        except (OSError, ValueError) as exc:
            raise HarnessConfigurationError(
                f"MCP server '{name}' failed to start: {exc}"
            ) from exc


def _send(process: subprocess.Popen, message: dict) -> None:
    process.stdin.write(json.dumps(message) + "\n")
    process.stdin.flush()


def _bounded(work, deadline: float, timeout_message: str):
    """Run blocking pipe I/O off-thread so the deadline and Ctrl-C still apply.

    A server that stops reading stdin can block a write indefinitely; the
    caller's cleanup kills the process, which releases the abandoned worker.
    """
    outcome: queue.Queue[tuple[bool, object]] = queue.Queue(maxsize=1)

    def run() -> None:
        try:
            outcome.put((True, work()))
        except BaseException as exc:  # re-raised on the calling thread
            outcome.put((False, exc))

    threading.Thread(target=run, daemon=True).start()
    while True:
        if cancel.requested():
            raise McpPreflightInterrupted
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise HarnessConfigurationError(timeout_message)
        try:
            ok, value = outcome.get(timeout=min(0.05, remaining))
        except queue.Empty:
            continue
        if ok:
            return value
        raise value


def _exchange(
    process: subprocess.Popen,
    request: dict,
    name: str,
    deadline: float,
    timeout: float,
) -> dict:
    """Read the matching MCP response, ignoring intervening notifications.

    The response may carry ``result`` or ``error``; the caller decides what a
    rejection means.
    """

    def work() -> dict:
        _send(process, request)
        while True:
            line = process.stdout.readline()
            if not line:
                if cancel.requested():
                    raise McpPreflightInterrupted
                raise HarnessConfigurationError(
                    f"MCP server '{name}' exited before answering {request['method']}"
                )
            response = json.loads(line)
            if not isinstance(response, dict):
                raise HarnessConfigurationError(
                    f"MCP server '{name}' sent an invalid response"
                )
            if "method" in response and "id" in response:
                # We advertise no client capabilities, so a server-initiated
                # request other than ping cannot be served. Reply rather than
                # leaving the server waiting; IDs are independent in each
                # direction.
                _send(
                    process,
                    {
                        "jsonrpc": "2.0",
                        "id": response["id"],
                        **(
                            {"result": {}}
                            if response["method"] == "ping"
                            else {
                                "error": {
                                    "code": -32601,
                                    "message": "Method not found",
                                }
                            }
                        ),
                    },
                )
                continue
            if response.get("id") == request["id"]:
                # What the agents' JSON-RPC clients drop: either way the agent
                # would never see this answer. ``error: null`` beside a result
                # is let through, as codex accepts it.
                if response.get("jsonrpc") != "2.0" or (
                    "result" in response and response.get("error") is not None
                ):
                    raise HarnessConfigurationError(
                        f"MCP server '{name}' sent an invalid response to "
                        f"{request['method']}"
                    )
                return response

    return _bounded(
        work,
        deadline,
        f"MCP server '{name}' did not answer {request['method']} within "
        f"{timeout:g} seconds",
    )


def _list_tools(
    process: subprocess.Popen,
    request_id: int,
    name: str,
    deadline: float,
    timeout: float,
    meta: dict | None = None,
) -> dict:
    request: dict = {"jsonrpc": "2.0", "id": request_id, "method": "tools/list"}
    if meta is not None:
        request["params"] = {"_meta": meta}
    response = _exchange(process, request, name, deadline, timeout)
    result = response.get("result")
    if (
        not isinstance(result, dict)
        or not isinstance(result.get("tools"), list)
        # Claude Code ignores a 2026-07-28 reply that carries ``error: null``
        # beside its result; codex accepts one on the legacy path.
        or (meta is not None and "error" in response)
    ):
        raise HarnessConfigurationError(f"MCP server '{name}' did not list tools")
    # The agents' MCP clients refuse a tool without these, so it would never
    # reach the agent.
    if not all(
        isinstance(tool, dict)
        and isinstance(tool.get("name"), str)
        and isinstance(tool.get("inputSchema"), dict)
        for tool in result["tools"]
    ):
        raise HarnessConfigurationError(
            f"MCP server '{name}' listed a tool without a name and inputSchema"
        )
    return result


def _initialized(
    process: subprocess.Popen,
    result: object,
    name: str,
    deadline: float,
    timeout: float,
) -> None:
    """Check a server that answered ``initialize``, as the agent's client would."""
    if (
        not isinstance(result, dict)
        or result.get("protocolVersion") not in _KNOWN_PROTOCOL_VERSIONS
        or not isinstance(result.get("capabilities"), dict)
        or not isinstance(result.get("serverInfo"), dict)
        or not isinstance(result["serverInfo"].get("name"), str)
        or not isinstance(result["serverInfo"].get("version"), str)
    ):
        raise HarnessConfigurationError(
            f"MCP server '{name}' returned an invalid initialization"
        )
    _bounded(
        lambda: _send(
            process, {"jsonrpc": "2.0", "method": "notifications/initialized"}
        ),
        deadline,
        f"MCP server '{name}' did not accept "
        f"notifications/initialized within {timeout:g} seconds",
    )
    if "tools" in result["capabilities"]:
        _list_tools(process, 2, name, deadline, timeout)


def _discover(
    process: subprocess.Popen,
    name: str,
    deadline: float,
    timeout: float,
    *,
    speaks_modern: bool,
) -> None:
    """Check a server that rejected ``initialize`` as a 2026-07-28 server.

    Discovery runs only after that rejection so a legacy server never sees a
    request it may not handle (docs/adr/0035).
    """
    # A legacy server may stay silent or exit on an unknown request; either way
    # it already rejected initialize, so wait only briefly before saying so.
    try:
        response = _exchange(
            process,
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "server/discover",
                "params": {"_meta": _MODERN_META},
            },
            name,
            min(deadline, time.monotonic() + _DISCOVER_TIMEOUT),
            _DISCOVER_TIMEOUT,
        )
    except (HarnessConfigurationError, ValueError) as exc:
        raise HarnessConfigurationError(
            f"MCP server '{name}' rejected initialize"
        ) from exc
    result = response.get("result")
    error = response.get("error")
    if isinstance(error, dict) and error.get("code") == _UNSUPPORTED_PROTOCOL_VERSION:
        data = error.get("data")
        raise _unsupported(
            name, data.get("supported") if isinstance(data, dict) else None
        )
    if "error" in response or not (
        isinstance(result, dict)
        and isinstance(result.get("supportedVersions"), list)
        and isinstance(result.get("capabilities"), dict)
    ):
        raise HarnessConfigurationError(f"MCP server '{name}' rejected initialize")
    if _MODERN_PROTOCOL_VERSION not in result["supportedVersions"]:
        raise _unsupported(name, result["supportedVersions"])
    if not speaks_modern:
        raise HarnessConfigurationError(
            f"MCP server '{name}' speaks only MCP {_MODERN_PROTOCOL_VERSION}, "
            "which this backend's agent cannot connect to. Use a server that "
            "also answers initialize, or a backend that speaks "
            f"{_MODERN_PROTOCOL_VERSION}."
        )
    if "tools" not in result["capabilities"]:
        return
    tools = _list_tools(process, 3, name, deadline, timeout, _MODERN_META)
    # 2026-07-28 requires these fields, and Claude Code drops the server's tools
    # when one is missing.
    ttl = tools.get("ttlMs")
    if (
        tools.get("resultType") != "complete"
        or type(ttl) not in (int, float)
        or not 0 <= ttl <= _MAX_SAFE_INTEGER
        or ttl != int(ttl)
        or tools.get("cacheScope") not in ("public", "private")
    ):
        raise HarnessConfigurationError(
            f"MCP server '{name}' listed tools without a valid resultType, ttlMs "
            "or cacheScope"
        )


def _unsupported(name: str, supported: object) -> HarnessConfigurationError:
    """A live 2026-era server that shares no protocol version with preflight."""
    message = f"MCP server '{name}' does not support MCP {_MODERN_PROTOCOL_VERSION}"
    if isinstance(supported, list) and supported:
        message += f" (it supports {', '.join(map(str, supported))})"
    return HarnessConfigurationError(message)


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


def merge_user_servers(
    user_servers: object, declared: dict[str, dict], ctx: RunContext
) -> dict[str, dict]:
    """The attempt's MCP servers: the user's own when loading customizations,
    minus any name the spec declares (ablated ones included), with the declared
    servers on top. Just the declared servers when isolated (docs/adr/0028)."""
    if not ctx.user_customizations or not isinstance(user_servers, dict):
        return dict(declared)
    kept = {n: e for n, e in user_servers.items() if n not in ctx.spec_mcp_names}
    return {**kept, **declared}


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
