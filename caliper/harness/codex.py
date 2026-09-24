from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from typing import Callable

import tomli_w

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10, where tomllib is not yet stdlib
    import tomli as tomllib

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

        The user's real config is read, its top-level ``model`` dropped (the
        seeded config never pins a model over the invocation, docs/adr/0012) and
        its ``mcp_servers`` replaced by exactly the declared ``mcp:`` servers.
        That is the tool-environment normalization: an attempt sees only the
        spec's servers, never the user's personal ones — even though codex is
        otherwise stateless, because the leak comes from seeding the real config.
        When neither a real config nor a declared server exists, nothing is
        written (the CLI falls back to its own defaults). The file may now hold
        resolved secrets, so it is kept ``0600``.

        Under ``--inherit-mcp`` the user's servers are kept, except any whose
        name the spec declares, ablated or not: the spec wins a name clash
        (docs/adr/0028).

        Read with a real TOML parser and written back whole, so every way TOML
        can spell a server — tables, dotted keys, an inline ``mcp_servers = {…}``
        — is handled alike, and a multi-line value never reads as a header. The
        user's comments and layout don't survive; the copy is the attempt's.
        """
        servers = self._translate_mcp_servers(ctx)
        real_exists = real_config.exists()
        if not real_exists and not servers:
            return

        config: dict = {}
        if real_exists:
            try:
                config = tomllib.loads(real_config.read_text())
            except tomllib.TOMLDecodeError as exc:
                raise HarnessConfigurationError(
                    f"Codex's config at {real_config} is not valid TOML ({exc}).\n\n"
                    "caliper copies it into each attempt, and codex would refuse "
                    "it there too. Fix the file, then rerun caliper."
                ) from exc
        config.pop("model", None)
        user_servers = config.pop("mcp_servers", None)
        kept = (
            {
                name: entry
                for name, entry in user_servers.items()
                if name not in ctx.spec_mcp_names
            }
            if ctx.inherit_mcp and isinstance(user_servers, dict)
            else {}
        )
        merged = {**kept, **servers}
        if merged:
            config["mcp_servers"] = merged

        codex_home.mkdir(parents=True, exist_ok=True)
        dst = codex_home / "config.toml"
        dst.write_text(tomli_w.dumps(config))
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

    def _inherited_mcp_servers(
        self, proc: ProcessResult, ctx: RunContext
    ) -> list[str] | None:
        """The user's servers left in the attempt's config, plus the hosted apps.

        Read off the ``config.toml`` this attempt actually ran with, minus the
        spec's servers. Codex's hosted connectors all surface under one server,
        ``codex_apps``, which counts only for a ChatGPT login (an API key carries
        no connectors) whose config did not turn the ``apps`` feature off.
        """
        codex_home = Path(ctx.isolated_home) / ".codex"
        config_path = codex_home / "config.toml"
        config = tomllib.loads(config_path.read_text()) if config_path.exists() else {}
        servers = config.get("mcp_servers")
        names = set(servers) if isinstance(servers, dict) else set()
        features = config.get("features")
        apps_off = isinstance(features, dict) and features.get("apps") is False
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
