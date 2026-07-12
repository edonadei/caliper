"""Shared MCP helpers used by every backend that materializes ``mcp:`` servers.

The one thing every MCP-capable backend must agree on is *what ``${VAR}`` means
and how an unset var fails*: a declared server's secrets are referenced by host
environment variable and resolved at the harness boundary (never written into
the committed spec), so an unset var is a configuration error surfaced here
rather than an opaque connect-time failure. Keeping this in one module is what
guarantees claude-code and hermes (and later codex) resolve secrets identically.
"""

from __future__ import annotations

import os
import re

from caliper.harness.base import HarnessConfigurationError

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
