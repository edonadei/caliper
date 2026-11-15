from __future__ import annotations

import contextlib
import json
import os
import re
import secrets
import sqlite3
import string
from collections.abc import Callable
from datetime import datetime
from functools import partial
from pathlib import Path

from caliper.backends import mcp_tool_name
from caliper.harness.base import (
    AgentReport,
    CliHarness,
    ConversationTurn,
    HarnessConfigurationError,
    ProcessResult,
    PromptCall,
    RunContext,
    stream_events,
)
from caliper.harness.cli_config import JSONC, CliConfig
from caliper.harness.history import (
    PlantedSession,
    PlantedSkill,
    Session,
    SessionFormat,
)
from caliper.harness.mcp import ResolvedMcpServer
from caliper.harness.refusal import AUTH_MARKERS, ConfigSignal
from caliper.harness.user_layer import copy_user_tree
from caliper.schema.results import TokenUsage
from caliper.schema.spec import PlantedShellCall
from caliper.skills import frontmatter
from caliper.workdir import StepCancelled

# Errors only, on stderr: opencode's stream reports a failed run as a generic
# "Unexpected server error", and names the cause only in its log.
_LOG_ERRORS = ("--print-logs", "--log-level", "ERROR")
# A fixed title, so opencode doesn't spend a call on its small model naming the
# session: tokens the attempt would record but the skill never asked for.
_TITLE = ("--title", "caliper")

# An attempt or a judge call never upgrades the operator's opencode mid-run.
_NO_AUTOUPDATE = {"OPENCODE_DISABLE_AUTOUPDATE": "1"}


def _opencode_entry(server: ResolvedMcpServer) -> dict:
    """One declared server in opencode's ``mcp`` shape.

    opencode names the transport (``local`` or ``remote``), takes a stdio
    server's command and arguments as one list, and calls its env
    ``environment``.
    """
    if server.is_remote:
        entry: dict = {"type": "remote", "url": server.url}
        if server.headers:
            entry["headers"] = server.headers
        return entry
    entry = {"type": "local", "command": [server.command, *server.args]}
    if server.env:
        entry["environment"] = server.env
    return entry


_CONFIG_FILES = ("config.json", "opencode.json", "opencode.jsonc")
_HTTP_URL = re.compile(r"^https?://", re.IGNORECASE)
_MD = frozenset({".md"})
_JS_TS = frozenset({".js", ".ts"})

# Each directory a user's opencode keeps beside its config file, staged when an
# attempt loads user customizations: name -> (inventory kind, recursive, suffixes).
_USER_DIR_SPECS: dict[str, tuple[str, bool, frozenset[str]]] = {
    "agent": ("settings", True, _MD),
    "agents": ("settings", True, _MD),
    "command": ("settings", True, _MD),
    "commands": ("settings", True, _MD),
    "mode": ("settings", False, _MD),
    "modes": ("settings", False, _MD),
    "plugin": ("plugin", False, _JS_TS),
    "plugins": ("plugin", False, _JS_TS),
    "tool": ("settings", False, _JS_TS),
    "tools": ("settings", False, _JS_TS),
}


def _model_string(value: object) -> str | None:
    """A configured model in ``provider/model`` form, from string or V2 object."""
    if isinstance(value, str):
        return value if value.strip() else None
    if isinstance(value, dict):
        provider = value.get("providerID")
        model = value.get("model")
        if (
            isinstance(provider, str)
            and provider.strip()
            and isinstance(model, str)
            and model.strip()
        ):
            return f"{provider}/{model}"
    return None


def _is_mcp_server_entry(entry: dict) -> bool:
    """Whether ``entry`` is a V1 server object rather than V2 ``mcp.servers``."""
    return any(
        key in entry and not isinstance(entry[key], dict) for key in ("type", "enabled")
    )


def _normalize_mcp(normalized: dict) -> None:
    mcp = normalized.get("mcp")
    if not isinstance(mcp, dict):
        return
    servers = mcp.get("servers")
    v2_servers = isinstance(servers, dict) and not _is_mcp_server_entry(servers)
    timeout = mcp.get("timeout")
    v2_timeout = (
        isinstance(timeout, dict)
        and not _is_mcp_server_entry(timeout)
        and (
            not timeout
            or any(k in timeout for k in ("startup", "catalog", "execution"))
        )
    )
    if not (v2_servers or v2_timeout):
        return
    rest = {
        k: v
        for k, v in mcp.items()
        if not ((k == "servers" and v2_servers) or (k == "timeout" and v2_timeout))
    }
    normalized["mcp"] = {**(servers if v2_servers else {}), **rest}
    if (
        v2_timeout
        and "startup" not in timeout
        and isinstance(catalog := timeout.get("catalog"), int)
        and catalog == timeout.get("execution")
    ):
        exp = normalized.get("experimental")
        if exp is None or isinstance(exp, dict):
            merged_exp = dict(exp) if isinstance(exp, dict) else {}
            merged_exp.setdefault("mcp_timeout", catalog)
            normalized["experimental"] = merged_exp


def _normalize_named_entries(
    normalized: dict, v2_key: str, v1_key: str, *, rename_system: bool = False
) -> None:
    entries = normalized.pop(v2_key, None)
    if not isinstance(entries, dict):
        return
    current = normalized.get(v1_key)
    if v1_key in normalized and not isinstance(current, dict):
        return
    merged = dict(current) if isinstance(current, dict) else {}
    for name, spec in entries.items():
        if name in merged or not isinstance(spec, dict):
            continue
        lowered = dict(spec)
        if rename_system and "system" in lowered:
            system = lowered.pop("system")
            lowered.setdefault("prompt", system)
        if isinstance(lowered.get("model"), dict) and (
            model := _model_string(lowered["model"])
        ):
            lowered["model"] = model
        merged[name] = lowered
    if merged or isinstance(current, dict):
        normalized[v1_key] = merged


def _normalize_config(config: dict) -> dict:
    """Apply OpenCode's per-file V2 shorthands before merging across config files."""
    normalized = dict(config)
    _normalize_mcp(normalized)
    permission = normalized.get("permission")
    if isinstance(permission, str):
        normalized["permission"] = {"*": permission}
    if isinstance(normalized.get("model"), dict) and (
        lowered := _model_string(normalized["model"])
    ):
        normalized["model"] = lowered
    skills = normalized.get("skills")
    if isinstance(skills, list) and all(isinstance(item, str) for item in skills):
        normalized["skills"] = {
            "paths": [s for s in skills if not _HTTP_URL.match(s)],
            "urls": [s for s in skills if _HTTP_URL.match(s)],
        }
    _normalize_named_entries(normalized, "agents", "agent", rename_system=True)
    _normalize_named_entries(normalized, "commands", "command")
    return normalized


# Isolated, the provider definitions and the default model survive: with no
# model named, the attempt runs what the operator's own `opencode run` would,
# as pi's does (docs/adr/0012).
OPENCODE_CONFIG = CliConfig(
    path="opencode.json",
    codec=JSONC,
    connection_keys=frozenset(
        {"provider", "enabled_providers", "disabled_providers", "model", "small_model"}
    ),
    servers_key="mcp",
    entry=_opencode_entry,
    source_paths=_CONFIG_FILES,
    normalize=_normalize_config,
)


class OpenCodeHarness(CliHarness):
    """CLI-subprocess backend for the `opencode` coding agent.

    Drives ``opencode run --format json`` with the prompt on stdin: an argv
    prompt containing a space reaches the agent wrapped in quotes. Skills are
    discovered under the config home's ``skills/`` and opened with opencode's
    ``skill`` tool.
    """

    def __init__(self, model: str | None = None, effort: str | None = None) -> None:
        self._model = model
        self._effort = effort

    supports_mcp = True
    cli_config = OPENCODE_CONFIG
    # opencode has no variable that moves its config home: it reads
    # ``$XDG_CONFIG_HOME/opencode``, which the isolated env leaves unset, so the
    # attempt's is inside its HOME. ``OPENCODE_CONFIG_DIR`` only adds a directory.
    config_home = ".config/opencode"
    # Its logins and sessions live apart, under ``$XDG_DATA_HOME/opencode``.
    data_home = ".local/share/opencode"
    user_rules = ("AGENTS.md",)
    user_settings_files = _CONFIG_FILES
    activation_tool_names = frozenset({"skill"})
    cli_name = "opencode"
    cli_path_env_var = "OPENCODE_CLI_PATH"
    cli_unavailable_message = (
        "opencode CLI is not available for the `opencode` backend.\n\n"
        "Install opencode (`npm install -g opencode-ai`) and authenticate a "
        "provider (`opencode auth login`), or set `OPENCODE_CLI_PATH` to the "
        "opencode binary, then rerun caliper."
    )

    @property
    def name(self) -> str:
        return "opencode"

    def _default_config_home(self) -> Path:
        return _xdg("XDG_CONFIG_HOME", ".config") / "opencode"

    def _ensure_ready(self, ctx: RunContext) -> None:
        # The XDG homes are opencode's config-home variables: one set to
        # something that isn't a directory would seed nothing, and surface only
        # as each attempt failing to log in (docs/adr/0038).
        for variable in _XDG_HOMES:
            selected = os.environ.get(variable, "").strip()
            if selected and not Path(selected).expanduser().is_dir():
                raise HarnessConfigurationError(
                    f"{variable} is set to {selected}, which is not a "
                    "directory.\n\ncaliper reads opencode's login and config "
                    "from there, as opencode itself does. Point "
                    f"{variable} at an existing directory, or unset it to use "
                    "the default, then rerun caliper."
                )
        super()._ensure_ready(ctx)

    def user_data_home(self) -> Path:
        """Where the operator's opencode keeps its logins."""
        return _xdg("XDG_DATA_HOME", ".local/share") / "opencode"

    def user_path(self, isolated: Path, ctx: RunContext) -> Path:
        try:
            inside = isolated.relative_to(Path(ctx.isolated_home) / self.data_home)
        except ValueError:
            return super().user_path(isolated, ctx)
        return self.user_data_home() / inside

    def seed_files(self, ctx: RunContext) -> list[Path]:
        # The stored logins, copied verbatim. opencode.json is absent: it is
        # rewritten rather than copied (``cli_config``).
        return [Path(ctx.isolated_home) / self.data_home / "auth.json"]

    def user_skill_sources(self, ctx: RunContext) -> list[Path]:
        # opencode discovers skills under both ``skills/`` and ``skill/``.
        return [*super().user_skill_sources(ctx), self.user_config_home() / "skill"]

    def stage_plugins(self, ctx: RunContext) -> tuple[list[str], dict[str, str]]:
        real = self.user_config_home()
        home = self.attempt_config_home(ctx)
        files: list[str] = []
        for name, (kind, recursive, suffixes) in _USER_DIR_SPECS.items():
            if not (real / name).is_dir():
                continue
            copied = home / name
            copy_user_tree(real / name, copied)
            if _has_loadable_files(copied, recursive=recursive, suffixes=suffixes):
                files.append(f"{kind}:{name}/")
        return files, {}

    def _has_wellknown_auth(self, ctx: RunContext) -> bool:
        auth = Path(ctx.isolated_home) / self.data_home / "auth.json"
        try:
            entries = json.loads(auth.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return False
        return isinstance(entries, dict) and any(
            isinstance(value, dict) and value.get("type") == "wellknown"
            for value in entries.values()
        )

    def _prepare(self, ctx: RunContext) -> None:
        if not self._has_wellknown_auth(ctx):
            return
        if not ctx.user_customizations:
            raise HarnessConfigurationError(
                "opencode's wellknown login imports remote settings that "
                "Caliper cannot isolate.\n\n"
                "Run with --user-customizations to load those settings, or "
                "configure provider API/OAuth credentials with "
                "`opencode auth login`, then retry the eval."
            )
        if ctx.spec_mcp_names or ctx.mcp_servers:
            raise HarnessConfigurationError(
                "opencode's wellknown login imports remote MCP settings that "
                "Caliper cannot merge with declared or ablated mcp: servers.\n\n"
                "Configure provider API/OAuth credentials with "
                "`opencode auth login`, or run without declared mcp: servers, "
                "then retry the eval. Remote settings can otherwise override "
                "server definitions or restore an ablated server."
            )

    @property
    def session_format(self) -> SessionFormat:
        return _OpenCodeSession(version=self.cli_version)

    def _command(
        self, ctx: RunContext, planted: PlantedSession | None
    ) -> tuple[list[str], str | None, Callable[[], None] | None]:
        opencode = self.cli_path() or "opencode"
        model = ctx.model or (
            _resume_model(self.attempt_config_home(ctx))
            if planted is not None
            else None
        )
        # --auto approves what opencode would otherwise ask about, which in
        # `run` it rejects: the attempt runs unattended.
        cmd = [opencode, "run", "--format", "json", *_LOG_ERRORS, *_TITLE, "--auto"]
        if model:
            cmd += ["--model", model]
        if self._effort:
            cmd += ["--variant", self._effort]
        if planted is not None:
            # opencode resumes only a session in its database, so the planted
            # one is imported first. The prompt becomes the next user turn, and
            # the stream does not replay the planted ones.
            self._import(planted, ctx)
            cmd += ["--session", planted.id]
        return cmd, ctx.prompt, None

    def _import(self, planted: PlantedSession, ctx: RunContext) -> None:
        cmd = [self.cli_path() or "opencode", "import", str(planted.path)]
        proc = self._execute(
            cmd,
            env=self._environment(ctx),
            cwd=ctx.workdir,
            timeout=ctx.timeout,
            stdin=None,
        )
        if proc.cancelled:
            raise StepCancelled("history import")
        if proc.timed_out:
            raise HarnessConfigurationError(
                "opencode could not import the task's planted history: "
                f"import timed out after {ctx.timeout} seconds"
            )
        if proc.returncode != 0:
            said = _ANSI.sub("", proc.stderr or proc.stdout).strip()
            raise HarnessConfigurationError(
                "opencode could not import the task's planted history.\n\n"
                f"Caliper ran {' '.join(cmd)}, which returned:\n  {said}"
            )

    def _environment(self, ctx: RunContext) -> dict[str, str]:
        return self._isolated_env(ctx, extra=_NO_AUTOUPDATE)

    def _read(self, proc: ProcessResult, ctx: RunContext) -> AgentReport:
        servers = OPENCODE_CONFIG.servers(self.attempt_config_home(ctx))
        report = self._read_stream(proc.stdout, servers)
        # A wellknown login can add remote servers that the local file cannot list.
        if not self._has_wellknown_auth(ctx):
            report.read_mcp_servers = lambda: servers
        report.resolved_effort = _session_variant(
            Path(ctx.isolated_home) / self.data_home / "opencode.db"
        )
        return report

    def _read_stream(self, stdout: str, servers: set[str]) -> AgentReport:
        """One walk of ``opencode run --format json``'s events.

        Each event carries one finished part: ``text`` is the agent speaking,
        ``tool_use`` a tool call with its result, and each ``step_finish`` its
        step's usage, summed into the run total. An ``error`` event is opencode
        itself failing, beside the agent.
        """
        report = AgentReport()
        usages: list[dict] = []
        for event in stream_events(stdout):
            etype = event.get("type")
            part = event.get("part")
            part = part if isinstance(part, dict) else {}
            if etype == "error":
                error = _stream_error(event)
                if error is not None:
                    report.cli_errors.append(error)
            elif etype == "text":
                text = part.get("text")
                if isinstance(text, str) and text.strip():
                    report.transcript.append(
                        ConversationTurn(role="assistant", content=text.strip())
                    )
                    report.final_output = text.strip()
            elif etype == "tool_use":
                report.transcript.extend(self._tool_turns(part, servers))
            elif etype == "step_finish" and isinstance(part.get("tokens"), dict):
                usages.append(part["tokens"])
        if usages:
            report.read_usage = partial(_usage, usages)
        return report

    def _tool_turns(self, part: dict, servers: set[str]) -> list[ConversationTurn]:
        """A finished tool call and its result, an MCP tool under caliper's name."""
        tool = part.get("tool")
        tool = _qualified(self.name, tool, servers) if isinstance(tool, str) else tool
        state = part.get("state")
        state = state if isinstance(state, dict) else {}
        args = state.get("input")
        output = state.get("output")
        if not isinstance(output, str):
            output = state.get("error") if isinstance(state.get("error"), str) else ""
        return [
            ConversationTurn(
                role="tool_use",
                content=f"[tool: {tool}]",
                tool_name=tool,
                tool_input=args if isinstance(args, dict) else None,
            ),
            ConversationTurn(role="tool_result", content=output, tool_output=output),
        ]

    # --- bare prompt call (the judge's half of the seam) -------------------

    def _prompt_command(self, prompt: str, model: str | None) -> PromptCall:
        opencode = self.cli_path()
        if not opencode:
            raise HarnessConfigurationError("opencode CLI not found")
        # No --auto: a judge has nothing to approve, and `run` rejects any ask.
        cmd = [opencode, "run", "--format", "json", *_LOG_ERRORS, *_TITLE]
        if model:
            cmd += ["--model", model]
        if self._effort:
            cmd += ["--variant", self._effort]
        return PromptCall(cmd, stdin=prompt)

    def _prompt_environment(self) -> dict[str, str]:
        return {**super()._prompt_environment(), **_NO_AUTOUPDATE}

    def _read_prompt(self, proc: ProcessResult) -> AgentReport:
        return self._read_stream(proc.stdout, set())

    def login_command(self) -> list[str]:
        return [self.cli_path() or "opencode", "auth", "login"]

    config_signals = (
        ConfigSignal(
            (*AUTH_MARKERS, "invalid x-api-key", "incorrect api key", "api key"),
            "opencode cannot run with the current authentication "
            "configuration.\n\n"
            "Caliper drives the local opencode CLI and reuses the logins it "
            "stores (`$XDG_DATA_HOME/opencode/auth.json`, else "
            "`~/.local/share/opencode/auth.json`). An API key opencode reads "
            "from your shell environment does not reach an attempt. The "
            "opencode CLI returned:\n"
            "  {text}\n\n"
            "Run `{login}`, verify `opencode run 'Reply OK'` works in your "
            "normal shell, then rerun caliper.",
            login=True,
        ),
    )

    def _diagnose(
        self, proc: ProcessResult, report: AgentReport, cli_text: str
    ) -> str | None:
        """Catch a model opencode does not know, so the run stops on it.

        A provider with no login has no models either, so opencode reports a
        model of an unauthenticated provider the same way.
        """
        if proc.returncode == 0:
            return None
        said = _MODEL_NOT_FOUND.search(cli_text)
        if said is None:
            return None
        return (
            "opencode cannot run the requested model.\n\n"
            "Caliper ran {cli}, which returned:\n"
            f"  {said[0]}\n\n"
            "Run `opencode models` for the models opencode can run, pass one as "
            "`--model opencode:<provider>/<model>`, or log in to its provider "
            "with `opencode auth login`, then retry the eval."
        )


_MODEL_NOT_FOUND = re.compile(r"Model not found: [^\"\n]*")
_ANSI = re.compile(r"\x1b\[[0-9;]*m")


_XDG_HOMES = ("XDG_CONFIG_HOME", "XDG_DATA_HOME")


def _xdg(variable: str, default: str) -> Path:
    """An XDG base directory the operator set, else its default under HOME."""
    selected = os.environ.get(variable, "").strip()
    return Path(selected).expanduser() if selected else Path.home() / default


def _has_loadable_files(
    directory: Path, *, recursive: bool, suffixes: frozenset[str]
) -> bool:
    """Whether ``directory`` holds a file opencode's scanner loads."""
    if not directory.is_dir():
        return False
    if recursive:
        for root, _, filenames in os.walk(directory, followlinks=True):
            if any(
                Path(name).suffix in suffixes and (Path(root) / name).is_file()
                for name in filenames
            ):
                return True
        return False
    return any(p.is_file() and p.suffix in suffixes for p in directory.iterdir())


def _iter_agent_markdown(home: Path) -> list[tuple[str, Path]]:
    """``(default_name, path)`` pairs for markdown agent/mode definitions."""
    entries: list[tuple[str, Path]] = []
    for name in ("agent", "agents"):
        base = home / name
        if not base.is_dir():
            continue
        for root, _, filenames in os.walk(base, followlinks=True):
            for filename in filenames:
                path = Path(root) / filename
                if path.suffix == ".md" and path.is_file():
                    rel = path.relative_to(base).with_suffix("").as_posix()
                    entries.append((rel, path))
    for name in ("mode", "modes"):
        base = home / name
        if not base.is_dir():
            continue
        for path in base.iterdir():
            if path.suffix == ".md" and path.is_file():
                entries.append((path.stem, path))
    return entries


def _has_agent_model(home: Path, config: dict) -> bool:
    """Whether ``home`` configures a model on the active primary agent."""
    default_agent = config.get("default_agent")
    agent = (
        default_agent.strip()
        if isinstance(default_agent, str) and default_agent.strip()
        else "build"
    )
    for key in ("agent", "mode"):
        section = config.get(key)
        if (
            isinstance(section, dict)
            and isinstance(entry := section.get(agent), dict)
            and _model_string(entry.get("model"))
        ):
            return True
    for default_name, path in _iter_agent_markdown(home):
        try:
            meta = frontmatter(path.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeDecodeError):
            continue
        declared = meta.get("name")
        name = declared if isinstance(declared, str) and declared else default_name
        if name == agent and _model_string(meta.get("model")):
            return True
    return False


def _resume_model(home: Path) -> str | None:
    """Top-level model to pin on a history resume when no agent model overrides it."""
    config = OPENCODE_CONFIG.read(home)
    if _has_agent_model(home, config):
        return None
    configured = _model_string(config.get("model"))
    if not configured:
        raise HarnessConfigurationError(
            "opencode history: requires a model selected explicitly "
            "or configured in opencode's config.\n\n"
            "Pass `--model opencode:<provider>/<model>`, or set "
            '`"model": "<provider>/<model>"` in your opencode config, '
            "then rerun caliper. Native automatic model selection "
            "cannot be used with a planted history."
        )
    return configured


def _sanitized(name: str) -> str:
    """A name as opencode spells it inside a tool name."""
    return re.sub(r"[^a-zA-Z0-9_-]", "_", name)


def _qualified(backend: str, tool: str, servers: set[str]) -> str:
    """``tool`` under caliper's ``mcp__<server>__<tool>`` if an MCP server gave it.

    opencode names an MCP tool ``<server>_<tool>``, so a known server is
    matched on that prefix, the longest one winning (``mail_archive`` over
    ``mail``). A built-in tool keeps its name.
    """
    matches = [s for s in servers if tool.startswith(_sanitized(s) + "_")]
    if not matches:
        return tool
    server = max(matches, key=len)
    return mcp_tool_name(backend, server, tool[len(_sanitized(server)) + 1 :])


def _usage(usages: list[dict]) -> TokenUsage:
    """Sum the steps' ``tokens`` into the run total.

    opencode's ``input`` is already non-cached and its cache split is
    disjoint; ``reasoning`` is counted apart from ``output``, so it is folded
    back in, as codex reports it.
    """
    totals = {"input": 0, "output": 0, "read": 0, "write": 0}
    for tokens in usages:
        cache = tokens.get("cache")
        cache = cache if isinstance(cache, dict) else {}
        for key, value in (
            ("input", tokens.get("input")),
            ("output", tokens.get("output")),
            ("output", tokens.get("reasoning")),
            ("read", cache.get("read")),
            ("write", cache.get("write")),
        ):
            if isinstance(value, int):
                totals[key] += value
    return TokenUsage(
        input_tokens=totals["input"],
        output_tokens=totals["output"],
        cache_read_tokens=totals["read"],
        cache_creation_tokens=totals["write"],
    )


def _stream_error(event: dict) -> str | None:
    """What an ``error`` event says: its name, its HTTP status, its message."""
    error = event.get("error")
    if not isinstance(error, dict):
        return None
    data = error.get("data")
    data = data if isinstance(data, dict) else {}
    message = data.get("message")
    if not isinstance(message, str) or not message.strip():
        return None
    status = data.get("statusCode")
    name = error.get("name") or "error"
    label = f"{name} ({status})" if isinstance(status, int) else str(name)
    return f"{label}: {message.strip()}"


_BASE62 = string.digits + string.ascii_letters


def _identifier(prefix: str, now: datetime, counter: int, *, descending: bool) -> str:
    """An opencode id: 12 hex digits of time and counter, then 14 random ones.

    Message and part ids ascend, so a planted turn sorts before the turns the
    attempt adds. A session id descends: opencode's provider refused to answer
    a resumed session whose id did not (1.19.5).
    """
    value = int(now.timestamp() * 1000) * 0x1000 + counter
    if descending:
        value = ~value
    value &= (1 << 48) - 1
    suffix = "".join(secrets.choice(_BASE62) for _ in range(14))
    return f"{prefix}_{value:012x}{suffix}"


def _text(value: str) -> dict:
    return {"type": "text", "text": value}


# The model a planted turn names: none that exists, so none is run.
_PLANTED_MODEL = {"providerID": "caliper", "modelID": "planted"}

# A planted assistant turn's usage: it cost the attempt nothing.
_NO_TOKENS = {"input": 0, "output": 0, "reasoning": 0, "cache": {"read": 0, "write": 0}}


class _OpenCodeSession(SessionFormat):
    """An opencode session as ``opencode export`` writes it, for ``opencode import``.

    One JSON document: the session's ``info``, then each message's ``info`` and
    ``parts``. Fields as a resume was seen to need them (1.19.5). A planted
    assistant turn names no real model: the resumed turn runs on the
    attempt's own. A planted shell call is a finished ``bash`` tool part.

    The session records the version of the opencode that imports it: opencode's
    provider refused to answer a resumed session with none (1.19.5).
    """

    def __init__(self, version: Callable[[], str | None] = lambda: None) -> None:
        self._version = version

    def session_id(self, now: datetime) -> str:
        return _identifier("ses", now, 0, descending=True)

    def path(self, session: Session) -> Path:
        return session.config_dir / "planted" / f"{session.id}.json"

    def header(self, session: Session) -> dict:
        millis = int(session.now.timestamp() * 1000)
        return {
            "id": session.id,
            "slug": "caliper-planted",
            "projectID": "global",
            "directory": session.cwd,
            "title": "caliper",
            "version": _version_number(self._version()),
            "time": {"created": millis, "updated": millis},
        }

    def user(self, text: str) -> list[dict]:
        info = {"role": "user", "agent": "build", "model": _PLANTED_MODEL}
        return [{"info": info, "parts": [_text(text)]}]

    def skill(self, skill: PlantedSkill) -> list[dict]:
        """What opencode expands ``/name`` to: the skill's body, then its base."""
        text = (
            f"{skill.body.strip()}\n\n\n"
            f"Base directory for this skill: {skill.skill_md.parent}\n"
            "Relative paths in this skill (e.g., scripts/, references/) are "
            "relative to this base directory."
        )
        return self.user(f"{text}\n\n{skill.args}" if skill.args else text)

    def assistant(
        self, text: str | None, calls: list[tuple[str, PlantedShellCall]]
    ) -> list[dict]:
        parts = [_text(text)] if text else []
        parts += [
            {
                "type": "tool",
                "tool": "bash",
                "callID": call_id,
                "state": {
                    "status": "completed",
                    "input": {"command": call.run, "description": call.run},
                    "output": call.output,
                    "title": call.run,
                    "metadata": {},
                },
            }
            for call_id, call in calls
        ]
        info = {
            "role": "assistant",
            "mode": "build",
            "agent": "build",
            "cost": 0,
            "tokens": _NO_TOKENS,
            **_PLANTED_MODEL,
            "finish": "stop",
        }
        return [{"info": info, "parts": parts}]

    def envelope(self, message: dict, previous: dict | None, session: Session) -> dict:
        index = 1 if previous is None else previous["_index"] + 1
        millis = int(session.now.timestamp() * 1000)
        message_id = _identifier("msg", session.now, index * 100, descending=False)
        info = {**message["info"], "id": message_id, "sessionID": session.id}
        if info["role"] == "assistant":
            # An assistant message answers the user message before it.
            parent = previous
            while parent is not None and parent["info"]["role"] != "user":
                parent = parent["_previous"]
            if parent is not None:
                info["parentID"] = parent["info"]["id"]
            info["path"] = {"cwd": session.cwd, "root": session.cwd}
            info["time"] = {"created": millis, "completed": millis}
        else:
            info["time"] = {"created": millis}
        parts = []
        for i, part in enumerate(message["parts"], start=1):
            part = {
                **part,
                "id": _identifier(
                    "prt", session.now, index * 100 + i, descending=False
                ),
                "sessionID": session.id,
                "messageID": message_id,
            }
            if part["type"] == "tool":
                part["state"] = {
                    **part["state"],
                    "time": {"start": millis, "end": millis},
                }
            parts.append(part)
        return {"info": info, "parts": parts, "_index": index, "_previous": previous}

    def render(self, lines: list[dict]) -> str:
        header, *messages = lines
        exported = [{"info": m["info"], "parts": m["parts"]} for m in messages]
        return json.dumps({"info": header, "messages": exported})


OPENCODE_SESSION = _OpenCodeSession()


def _version_number(said: str | None) -> str:
    """The bare version in what ``opencode --version`` printed, else ``0.0.0``."""
    match = re.search(r"\d+\.\d+\.\d+\S*", said or "")
    return match[0] if match else "0.0.0"


def _session_variant(database: Path) -> str | None:
    """The variant of the last assistant message, from the attempt's database.

    ``run --format json`` never names it; the stored message does.
    """
    if not database.is_file():
        return None
    try:
        with contextlib.closing(sqlite3.connect(database)) as db:
            rows = db.execute(
                "SELECT data FROM message ORDER BY time_created DESC"
            ).fetchall()
    except sqlite3.Error:
        return None
    for (data,) in rows:
        try:
            info = json.loads(data)
        except (TypeError, ValueError):
            continue
        if isinstance(info, dict) and info.get("role") == "assistant":
            variant = info.get("variant")
            return variant if isinstance(variant, str) and variant else None
    return None
