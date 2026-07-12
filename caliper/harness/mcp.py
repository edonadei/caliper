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

import os
import re
from dataclasses import dataclass, field

from caliper.harness.base import HarnessConfigurationError
from caliper.schema.spec import McpServer

# A ``${VAR}`` reference inside an MCP server field (stdio ``env`` values, remote
# ``headers`` values, a remote ``url``). Only this exact form is honored.
ENV_VAR_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


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
