"""The CLI config file an attempt runs with, rewritten from the user's own.

Some agents keep their MCP servers in the same file as their login and model
settings (codex's ``config.toml``, hermes' ``config.yaml``), so the file cannot
be copied verbatim like a ``seed_files`` entry (docs/adr/0012). A backend
declares that file as a :class:`CliConfig` and this module rewrites it into the
attempt's config home (docs/CONTEXT.md → Config home):

- isolated, only the connection keys survive, so the user's ambient state never
  reaches the attempt (docs/adr/0028);
- the servers become :func:`~caliper.harness.mcp.merge_user_servers` of the
  user's and the declared ones;
- the result is kept ``0600``, because it may hold resolved secrets
  (docs/adr/0009).

:func:`servers` reads back what was written: the file is the fact.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import tomli_w
import yaml

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10, where tomllib is not yet stdlib
    import tomli as tomllib

from caliper.harness.base import HarnessConfigurationError
from caliper.harness.mcp import ResolvedMcpServer, merge_user_servers, resolve_servers

if TYPE_CHECKING:
    from caliper.harness.base import RunContext


@dataclass(frozen=True)
class Codec:
    """How one config format is read and written."""

    name: str
    loads: Callable[[str], object]
    dumps: Callable[[dict], str]
    error: type[Exception]


TOML = Codec("TOML", tomllib.loads, tomli_w.dumps, tomllib.TOMLDecodeError)
YAML = Codec(
    "YAML",
    yaml.safe_load,
    lambda config: yaml.safe_dump(config, sort_keys=False),
    yaml.YAMLError,
)


@dataclass(frozen=True)
class CliConfig:
    """A backend's config file, as data (docs/adr/0020)."""

    # Relative to the config home, the user's and the attempt's alike.
    path: str
    codec: Codec
    # The keys an isolated attempt keeps: what the CLI needs to connect.
    connection_keys: frozenset[str]
    # The key holding the MCP servers.
    servers_key: str = "mcp_servers"
    # Keys dropped even when loading user customizations.
    dropped_keys: frozenset[str] = frozenset()
    # One declared server in this CLI's spelling.
    entry: Callable[[ResolvedMcpServer], dict] = ResolvedMcpServer.entry
    # Settings the attempt's copy always carries, merged over the user's: what
    # the attempt itself needs from the CLI, not a user setting.
    pinned: Callable[[], dict] = dict

    def write(self, ctx: RunContext, home: Path, *, backend: str, real: Path) -> None:
        """Write the attempt's copy into ``home``, its config home, from ``real``.

        ``real`` is the user's copy. Nothing is written when the user has no
        config, the spec declares no server and nothing is pinned. A config the
        CLI could not parse either is refused here.
        """
        declared = {
            name: self.entry(resolved)
            for name, resolved in resolve_servers(ctx.mcp_servers).items()
        }
        pinned = self.pinned()
        if not real.exists() and not declared and not pinned:
            return

        config = self._load(real, backend=backend) if real.exists() else {}
        servers = merge_user_servers(config.get(self.servers_key), declared, ctx)
        if not ctx.user_customizations:
            config = {k: v for k, v in config.items() if k in self.connection_keys}
        config = {k: v for k, v in config.items() if k not in self.dropped_keys}
        for key, value in pinned.items():
            current = config.get(key)
            if isinstance(current, dict) and isinstance(value, dict):
                value = {**current, **value}
            config[key] = value
        config.pop(self.servers_key, None)
        if servers:
            config[self.servers_key] = servers

        target = home / self.path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(self.codec.dumps(config), encoding="utf-8")
        target.chmod(0o600)

    def read(self, home: Path) -> dict:
        """The attempt's copy in ``home`` as written; empty when none was."""
        target = home / self.path
        if not target.exists():
            return {}
        loaded = self.codec.loads(target.read_text(encoding="utf-8"))
        return loaded if isinstance(loaded, dict) else {}

    def servers(self, home: Path) -> set[str]:
        """The MCP servers the attempt's copy in ``home`` holds."""
        servers = self.read(home).get(self.servers_key)
        return set(servers) if isinstance(servers, dict) else set()

    def _load(self, real: Path, *, backend: str) -> dict:
        try:
            loaded = self.codec.loads(real.read_text(encoding="utf-8"))
        except self.codec.error as exc:
            raise HarnessConfigurationError(
                f"{backend}'s config at {real} is not valid {self.codec.name} "
                f"({exc}).\n\n"
                f"caliper copies it into each attempt, and {backend} would refuse "
                "it there too. Fix the file, then rerun caliper."
            ) from exc
        return loaded if isinstance(loaded, dict) else {}
