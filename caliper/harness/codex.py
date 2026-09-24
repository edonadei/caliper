from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from typing import Callable, Iterator

import tomli_w

from caliper.harness.base import (
    ConversationTurn,
    CliHarness,
    HarnessConfigurationError,
    ProcessResult,
    PromptCall,
    PromptResult,
    RunContext,
)
from caliper.harness.mcp import resolve_servers
from caliper.schema.results import TokenUsage

CODEX_APP_CLI = Path("/Applications/Codex.app/Contents/Resources/codex")

# The ChatGPT login in auth.json carries the account's hosted connectors
# (mcp__codex_apps__*) and remote plugins, which stripping [mcp_servers] can't
# reach. A -c override beats any [features] table in the seeded config.toml,
# so neither an attempt nor the judge sees them (docs/adr/0026). An attempt run
# with --inherit-mcp leaves them on; the judge never does (docs/adr/0028).
NO_ACCOUNT_CONNECTORS = ("-c", "features.apps=false", "-c", "features.plugins=false")

# The server name codex's hosted connectors surface under
# (``mcp__codex_apps__<tool>``). Recorded as one inherited server: caliper cannot
# list the connectors behind it without driving the ChatGPT account.
CODEX_APPS_SERVER = "codex_apps"


class CodexHarness(CliHarness):
    def __init__(self, model: str | None = None) -> None:
        self._model = model

    supports_mcp = True
    cli_name = "codex"
    cli_path_env_var = "CODEX_CLI_PATH"

    @property
    def name(self) -> str:
        return "codex"

    def cli_candidates(self) -> tuple[Path, ...]:
        return (CODEX_APP_CLI,)

    def _ensure_ready(self, ctx: RunContext) -> None:
        if not self._cli_available():
            raise HarnessConfigurationError(
                "Codex CLI is not available for the `codex` backend.\n\n"
                "Caliper runs skills only through CLI agents. Install and "
                "authenticate the Codex CLI to run with `--model codex`. For API "
                "billing, configure the Codex CLI with an API key rather than "
                "selecting a separate backend."
            )

    def skills_root(self, ctx: RunContext) -> Path:
        return Path(ctx.isolated_home) / ".codex" / "skills"

    def seed_files(self, ctx: RunContext) -> list[tuple[Path, Path]]:
        real = Path.home() / ".codex"
        codex_home = Path(ctx.isolated_home) / ".codex"
        # config.toml is deliberately absent: it is rewritten rather than copied
        # (see ``_materialize_config``), so seeding it verbatim would leak the
        # user's ambient model pin and MCP servers into the attempt.
        return [(real / "auth.json", codex_home / "auth.json")]

    def _prepare(self, ctx: RunContext) -> None:
        self._materialize_config(
            ctx,
            Path(ctx.isolated_home) / ".codex",
            Path.home() / ".codex" / "config.toml",
        )

    def _command(
        self, ctx: RunContext
    ) -> tuple[list[str], str | None, Callable[[], None] | None]:
        # The prompt goes to the agent unmodified. Codex has no force-load flag,
        # and caliper no longer invents one: the skill is installed at
        # .codex/skills/<name>/ and the agent discovers it (docs/adr/0013).
        full_prompt = ctx.prompt
        codex = self.cli_path() or "codex"
        cmd = [
            codex,
            "exec",
            "--json",
            "--skip-git-repo-check",
            "--dangerously-bypass-approvals-and-sandbox",
            "--color",
            "never",
            *(() if ctx.inherit_mcp else NO_ACCOUNT_CONNECTORS),
            "-",
        ]
        if ctx.model:
            cmd[2:2] = ["--model", ctx.model]
        return cmd, full_prompt, None

    def _environment(self, ctx: RunContext) -> dict[str, str]:
        return self._isolated_env(ctx)

    def _cli_available(self) -> bool:
        codex = self.cli_path()
        return codex is not None and self._version_ok(codex, timeout=5)

    def _usage(self, proc: ProcessResult, ctx: RunContext) -> TokenUsage | None:
        """Read the last ``turn.completed`` event's ``usage``.

        Codex uses OpenAI semantics where ``input_tokens`` *includes*
        ``cached_input_tokens``, so we subtract to keep ``input_tokens``
        non-cached (the disjoint-fields contract). Codex has no cache-creation
        notion, and ``reasoning_output_tokens`` is already folded into
        ``output_tokens``.
        """
        latest: dict | None = None
        for line in proc.stdout.splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict) or event.get("type") != "turn.completed":
                continue
            usage = event.get("usage")
            if isinstance(usage, dict):
                latest = usage
        if latest is None:
            return None
        raw_input = latest.get("input_tokens")
        cached = latest.get("cached_input_tokens")
        non_cached = None
        if raw_input is not None:
            non_cached = raw_input - (cached or 0)
        return TokenUsage(
            input_tokens=non_cached,
            output_tokens=latest.get("output_tokens"),
            cache_read_tokens=cached,
            cache_creation_tokens=None,
        )

    def _parse_stream(self, stdout: str) -> tuple[list[ConversationTurn], str]:
        transcript: list[ConversationTurn] = []
        final_output = ""

        for line in stdout.splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue

            if not isinstance(event, dict):
                continue

            item = event.get("item")
            if not isinstance(item, dict):
                continue

            if item.get("type") == "agent_message":
                text = item.get("text", "")
                if text:
                    transcript.append(ConversationTurn(role="assistant", content=text))
                    final_output = text
                continue

            if event.get("type") != "item.completed":
                continue

            if item.get("type") == "command_execution":
                command = item.get("command", "")
                output = item.get("aggregated_output", "")
                exit_code = item.get("exit_code")
                status = item.get("status")
                tool_input = {"command": command} if command else {}
                transcript.append(
                    ConversationTurn(
                        role="tool_use",
                        content=f"[tool: shell] {command}",
                        tool_name="shell",
                        tool_input=tool_input,
                    )
                )
                result_parts = []
                if output:
                    result_parts.append(output)
                if exit_code is not None:
                    result_parts.append(f"exit_code={exit_code}")
                if status:
                    result_parts.append(f"status={status}")
                tool_output = "\n".join(result_parts)
                transcript.append(
                    ConversationTurn(
                        role="tool_result",
                        content=tool_output,
                        tool_output=tool_output,
                    )
                )
                continue

            if self._is_mcp_tool_call(item):
                transcript.extend(self._mcp_tool_turns(item))
                continue

            item_type = str(item.get("type") or "tool")
            transcript.append(
                ConversationTurn(
                    role="tool_use",
                    content=f"[tool: {item_type}]",
                    tool_name=item_type,
                    tool_input=item,
                )
            )

        return transcript, final_output

    @staticmethod
    def _is_mcp_tool_call(item: dict) -> bool:
        """True when a completed item is an MCP tool invocation we can name.

        Detected *structurally* — a ``server`` plus a ``tool``/``tool_name`` (the
        fields codex's ``McpToolCall`` carries) — rather than by the type label, so
        the qualified name is recoverable across codex builds. An ``mcp_tool_call``
        item lacking those fields falls through to the generic tool branch.
        """
        tool = item.get("tool") or item.get("tool_name")
        return bool(item.get("server") and tool)

    def _mcp_tool_turns(self, item: dict) -> list[ConversationTurn]:
        """Render an MCP tool call as claude-code-parity ``mcp__<server>__<tool>``.

        Emits a ``tool_use`` turn named ``mcp__<server>__<tool>`` (the doubled-
        underscore form codex shares with claude-code) so a backend-agnostic
        ``expect:``/``assert:`` on that name matches, plus a ``tool_result`` turn
        carrying any output/error/status the item reported.
        """
        server = item.get("server")
        tool = item.get("tool") or item.get("tool_name")
        qualified = f"mcp__{server}__{tool}"
        args = item.get("arguments")
        turns = [
            ConversationTurn(
                role="tool_use",
                content=f"[tool: {qualified}]",
                tool_name=qualified,
                tool_input=args if isinstance(args, dict) else item,
            )
        ]

        result = item.get("result")
        if result is None:
            result = item.get("output")
        parts: list[str] = []
        if result is not None:
            parts.append(result if isinstance(result, str) else json.dumps(result))
        error = item.get("error")
        if error:
            parts.append(error if isinstance(error, str) else json.dumps(error))
        status = item.get("status")
        if status:
            parts.append(f"status={status}")
        if parts:
            output = "\n".join(parts)
            turns.append(
                ConversationTurn(role="tool_result", content=output, tool_output=output)
            )
        return turns

    # --- bare prompt call (the judge's half of the seam) -------------------

    def _prompt_command(self, prompt: str, model: str | None) -> PromptCall:
        codex = self.cli_path()
        if not codex:
            raise HarnessConfigurationError("codex CLI not found")

        # `--output-last-message` writes the final answer to a file, which is
        # the only clean channel: codex's stdout is a noisy session log. The
        # file outlives the process so the reader can pick it up; the reader
        # closes over its path, and ``cleanup`` removes it however the call
        # ended — a timeout never reaches the reader.
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as output_file:
            output_path = Path(output_file.name)

        cmd = [
            codex,
            "exec",
            "--skip-git-repo-check",
            "--dangerously-bypass-approvals-and-sandbox",
            "--color",
            "never",
            "--output-last-message",
            str(output_path),
            *NO_ACCOUNT_CONNECTORS,
            "-",
        ]
        if model:
            cmd[2:2] = ["--model", model]
        return PromptCall(
            cmd,
            stdin=prompt,
            read=lambda proc: self._read_last_message(proc, model, output_path),
            cleanup=lambda: output_path.unlink(missing_ok=True),
        )

    def _read_last_message(
        self, proc: ProcessResult, model: str | None, output_path: Path
    ) -> PromptResult:
        raw = output_path.read_text().strip() if output_path.exists() else ""
        raw = raw or proc.stdout.strip()
        if proc.returncode != 0:
            detail = _extract_codex_error(proc.stderr) or _extract_codex_error(raw)
            message = detail or f"codex judge exited {proc.returncode}"
            return PromptResult.unclassified_failure(message, model)
        # Codex doesn't surface the resolved model in this mode, so we can only
        # report the one that was requested (None when its own default ran).
        return PromptResult(text=raw, resolved_model=model, error=None)

    def _materialize_config(
        self, ctx: RunContext, codex_home: Path, real_config: Path
    ) -> None:
        """Seed the isolated ``config.toml``: stripped user config + declared MCP.

        The user's real config is copied minus its top-level ``model =`` line and
        minus any ``[mcp_servers*]`` tables it carries; the declared ``mcp:``
        servers are then serialized as a fresh ``[mcp_servers.*]`` block. Rewriting
        the section wholesale is the tool-environment normalization: an attempt
        sees only the spec's servers, never the user's ambient personal ones — even
        though codex is otherwise stateless, because the leak comes from seeding the
        real config. When neither a real config nor a declared server exists,
        nothing is written (the CLI falls back to its own defaults). The file may
        now hold resolved secrets, so it is kept ``0600``.

        Under ``--inherit-mcp`` the user's ``[mcp_servers*]`` tables are kept,
        except any whose name the spec declares, ablated or not: the spec wins a
        name clash (docs/adr/0028).
        """
        base = ""
        real_exists = real_config.exists()
        servers = self._translate_mcp_servers(ctx)
        if real_exists:
            base = self._strip_seeded_config(
                real_config.read_text(),
                keep_servers=ctx.inherit_mcp,
                shadowed=ctx.spec_mcp_names,
            )

        if not real_exists and not servers:
            return

        parts: list[str] = []
        if base.strip():
            parts.append(base.rstrip("\n"))
        if servers:
            parts.append(tomli_w.dumps({"mcp_servers": servers}).rstrip("\n"))
        content = "\n\n".join(parts) + "\n" if parts else ""

        codex_home.mkdir(parents=True, exist_ok=True)
        dst = codex_home / "config.toml"
        dst.write_text(content)
        dst.chmod(0o600)

    def _translate_mcp_servers(self, ctx: RunContext) -> dict[str, dict]:
        """Translate the declared ``mcp:`` servers into codex's ``mcp_servers`` shape.

        The common rendering from ``resolve_servers`` (every ``${VAR}`` already
        interpolated at the harness boundary), plus codex's one spelling
        difference: a remote server's ``headers`` map is written as
        ``http_headers`` — static literal values, per
        docs/adr/0011-codex-remote-mcp-uses-static-http-headers-not-env-indirection.md.
        Codex infers its one streamable-HTTP transport from ``url``, so caliper's
        ``type`` is dropped (remote OAuth — which caliper's spec cannot express —
        is out of reach).
        """
        servers: dict[str, dict] = {}
        for name, resolved in resolve_servers(ctx.mcp_servers).items():
            entry = resolved.entry()
            if "headers" in entry:
                entry["http_headers"] = entry.pop("headers")
            servers[name] = entry
        return servers

    def _strip_seeded_config(
        self,
        config: str,
        *,
        keep_servers: bool = False,
        shadowed: frozenset[str] = frozenset(),
    ) -> str:
        """Drop the top-level ``model`` key and the user's MCP servers.

        The model line is stripped so the seeded config never pins a model over the
        caliper invocation; the ``mcp_servers`` entries are stripped so the user's
        personal servers are replaced (in ``_materialize_config``) by exactly the
        declared set. With ``keep_servers`` (``--inherit-mcp``) they stay, minus
        the ones named in ``shadowed`` — a declared server of the same name
        replaces them (or, if ablated, removes them), and TOML refuses a table
        defined twice. Line-based on purpose: it needs no TOML *reader*
        (unavailable on the 3.10 floor); :func:`_toml_lines` does the reading.
        """

        def dropped(server: str | None) -> bool:
            return not keep_servers or server in shadowed

        filtered: list[str] = []
        # Set on a header line; true for every line of a dropped table.
        dropping_table = False
        for line, table, kind, path in _toml_lines(config):
            if kind == "table":
                dropping_table = table[:1] == ["mcp_servers"] and (
                    dropped(table[1] if len(table) > 1 else None)
                )
            if dropping_table:
                continue
            if kind == "key":
                full = table + path
                # A server written as keys: `name = {…}` in a bare
                # [mcp_servers] table, or dotted from anywhere above it.
                if full[0] == "mcp_servers" and len(full) > 1 and dropped(full[1]):
                    continue
                if full == ["model"]:
                    continue
            filtered.append(line)
        return "\n".join(filtered) + ("\n" if config.endswith("\n") else "")

    def _inherited_mcp_servers(
        self, proc: ProcessResult, ctx: RunContext
    ) -> list[str] | None:
        """The user's servers left in the attempt's config, plus the hosted apps.

        Read off the ``config.toml`` this attempt actually ran with, minus the
        spec's servers, with the same reader that stripped it. Codex's hosted
        connectors all surface under one server, ``codex_apps``, which counts
        only for a ChatGPT login (an API key carries no connectors) whose config
        did not turn the ``apps`` feature off.
        """
        codex_home = Path(ctx.isolated_home) / ".codex"
        config_path = codex_home / "config.toml"
        config = config_path.read_text() if config_path.exists() else ""
        names: set[str] = set()
        apps_off = False
        for line, table, kind, path in _toml_lines(config):
            full = table + path if kind == "key" else table
            if full[:1] == ["mcp_servers"] and len(full) > 1:
                names.add(full[1])
            if kind == "key" and full == ["features", "apps"]:
                apps_off = _toml_value(line) == "false"
        if not apps_off and _chatgpt_login(codex_home / "auth.json"):
            names.add(CODEX_APPS_SERVER)
        return sorted(names - ctx.spec_mcp_names)

    def _diagnose(self, proc: ProcessResult, final_output: str) -> str | None:
        if proc.returncode == 0:
            return None

        text = "\n".join(part for part in (proc.stdout, proc.stderr) if part).strip()
        lowered = text.lower()

        model_markers = (
            "requires a newer version of codex",
            "please upgrade to the latest app or cli",
            "model is not supported",
            "model is not available",
        )
        if any(marker in lowered for marker in model_markers):
            summary = self._summarize_cli_configuration_error(text)
            return (
                "Codex CLI cannot run the requested model with this account or "
                "installed version.\n\n"
                "Caliper uses `codex exec` for `--model codex`. The Codex CLI "
                "returned:\n"
                f"  {summary}\n\n"
                "Pass `--model codex` (no model) to use the Codex CLI default, "
                "upgrade the Codex app "
                "or CLI, or choose a model supported by the installed Codex CLI "
                "and account, then retry the eval."
            )

        auth_markers = (
            "401 unauthorized",
            "not logged in",
            "please login",
            "please run /login",
            "authentication",
            "invalid api key",
            "api key",
            "subscription",
            "chatgpt account",
        )
        if any(marker in lowered for marker in auth_markers):
            return (
                "Codex CLI cannot run with the current subscription/authentication "
                "configuration.\n\n"
                "Caliper uses `codex exec` for `--model codex` and does not fall "
                "back to the OpenAI API. The Codex CLI returned:\n"
                f"  {text}\n\n"
                "Run `codex login` and verify `codex exec` works in your normal "
                "shell, then retry the eval. For API billing, configure the "
                "Codex CLI with an API key rather than selecting a separate "
                "backend."
            )

        if sys.platform == "darwin" and "operation not permitted" in lowered:
            return (
                "Codex CLI was blocked by the operating system while running under "
                "Caliper.\n\n"
                f"The Codex CLI returned:\n  {text}"
            )

        return None

    def _summarize_cli_configuration_error(self, text: str) -> str:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        version_line = next(
            (line for line in lines if line.startswith("OpenAI Codex")), None
        )
        error_lines = [line for line in lines if line.startswith("ERROR:")]
        detail_lines = [
            line
            for line in lines
            if (
                "requires a newer version of Codex" in line
                or "model is not supported" in line
                or "model is not available" in line
            )
            and not line.startswith(("stream error:", "ERROR:"))
        ]

        useful = []
        if version_line:
            useful.append(version_line)
        useful.extend(error_lines[:2])
        useful.extend(line for line in detail_lines[:2] if line not in useful)
        if useful:
            return "\n  ".join(useful[:5])
        return text[:500]


def _extract_codex_error(output: str) -> str | None:
    """Pull a readable failure message out of codex's noisy CLI output."""
    for line in reversed(output.splitlines()):
        line = line.strip()
        if not line:
            continue
        if line.startswith("ERROR:"):
            candidate = line.removeprefix("ERROR:").strip()
            message = _error_message_from_json(candidate)
            return f"codex judge failed: {message or candidate}"
        message = _error_message_from_json(line)
        if message:
            return f"codex judge failed: {message}"
    return None


def _error_message_from_json(candidate: str) -> str | None:
    try:
        payload = json.loads(candidate)
    except json.JSONDecodeError:
        return None

    error = payload.get("error")
    if isinstance(error, dict):
        message = error.get("message")
        if isinstance(message, str) and message:
            return message
    return None


def _toml_lines(
    config: str,
) -> Iterator[tuple[str, list[str], str | None, list[str]]]:
    """Walk a TOML file line by line: ``(line, table, kind, path)``.

    ``kind`` is ``"table"`` for a ``[header]``/``[[header]]`` (``path`` is then
    the header's own path, and ``table`` already is it), ``"key"`` for a
    ``key = value`` line (``path`` is the key's dotted path, relative to
    ``table``), and ``None`` for anything else. Keys may be bare or quoted, with
    whitespace around the dots. Enough of TOML to find tables and keys in a
    codex config, not a parser: a line inside a multi-line string is read as if
    it stood alone.
    """
    table: list[str] = []
    for line in config.splitlines():
        stripped = line.strip()
        if stripped.startswith("["):
            inner = stripped[2:] if stripped.startswith("[[") else stripped[1:]
            close = _outside_quotes(inner, "]")
            path = _toml_path(inner[:close]) if close >= 0 else None
            # An unreadable header still ends the previous table.
            table = path if path else ["<unreadable>"]
            yield line, table, "table", table
            continue
        equals = _outside_quotes(stripped, "=")
        path = _toml_path(stripped[:equals]) if equals > 0 else None
        if path and not stripped.startswith("#"):
            yield line, table, "key", path
        else:
            yield line, table, None, []


def _toml_path(text: str) -> list[str] | None:
    """Split a dotted TOML key (``a."b.c" . d``) into its segments, else ``None``."""
    segments: list[str] = []
    i, n = 0, len(text)
    while True:
        while i < n and text[i] in " \t":
            i += 1
        if i >= n:
            return None
        if text[i] in "\"'":
            end = text.find(text[i], i + 1)
            if end < 0:
                return None
            segments.append(text[i + 1 : end])
            i = end + 1
        else:
            start = i
            while i < n and (text[i].isalnum() or text[i] in "_-"):
                i += 1
            if i == start:
                return None
            segments.append(text[start:i])
        while i < n and text[i] in " \t":
            i += 1
        if i >= n:
            return segments
        if text[i] != ".":
            return None
        i += 1


def _outside_quotes(text: str, char: str) -> int:
    """The index of the first ``char`` not inside a quoted string, else ``-1``."""
    quote: str | None = None
    for i, ch in enumerate(text):
        if quote:
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
        elif ch == char:
            return i
    return -1


def _toml_value(line: str) -> str:
    """The value of a ``key = value`` line, comment and whitespace removed."""
    value = line[_outside_quotes(line, "=") + 1 :]
    comment = _outside_quotes(value, "#")
    return (value[:comment] if comment >= 0 else value).strip()


def _chatgpt_login(auth_path: Path) -> bool:
    """True when ``auth.json`` holds a ChatGPT login, the only kind with apps.

    A ChatGPT login stores OAuth ``tokens``; an API-key login does not, and
    brings no hosted connectors. Unreadable or absent counts as no login, so a
    connector set caliper cannot vouch for is never recorded.
    """
    try:
        auth = json.loads(auth_path.read_text())
    except (OSError, ValueError):
        return False
    return isinstance(auth, dict) and bool(auth.get("tokens"))
