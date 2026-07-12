from __future__ import annotations

import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Callable

import tomli_w

from caliper.harness.base import (
    ConversationTurn,
    CliHarness,
    HarnessConfigurationError,
    ProcessResult,
    PromptResult,
    RunContext,
)
from caliper.harness.mcp import resolve_servers
from caliper.schema.results import TokenUsage

CODEX_APP_CLI = Path("/Applications/Codex.app/Contents/Resources/codex")


class CodexHarness(CliHarness):
    def __init__(self, model: str | None = None) -> None:
        self._model = model

    supports_mcp = True

    @property
    def name(self) -> str:
        return "codex"

    def _ensure_ready(self, ctx: RunContext) -> None:
        if not self._cli_available():
            raise HarnessConfigurationError(
                "Codex CLI is not available for the `codex` backend.\n\n"
                "Caliper runs skills only through CLI agents. Install and "
                "authenticate the Codex CLI to run with `--model codex`. For API "
                "billing, configure the Codex CLI with an API key rather than "
                "selecting a separate backend."
            )

    def _prepare(self, ctx: RunContext) -> None:
        self._copy_codex_config(ctx)

    def _command(
        self, ctx: RunContext
    ) -> tuple[list[str], str | None, Callable[[], None] | None]:
        full_prompt = self._inject_skill(ctx.prompt, ctx.skill_path)
        codex = self._codex_command() or "codex"
        cmd = [
            codex,
            "exec",
            "--json",
            "--skip-git-repo-check",
            "--dangerously-bypass-approvals-and-sandbox",
            "--color",
            "never",
            "-",
        ]
        if ctx.model:
            cmd[2:2] = ["--model", ctx.model]
        return cmd, full_prompt, None

    def _environment(self, ctx: RunContext) -> dict[str, str]:
        return self._build_env(ctx.isolated_home, ctx.extra_path)

    def _inject_skill(self, prompt: str, skill_path: str | None) -> str:
        if not skill_path:
            return prompt

        skill_src = Path(skill_path).expanduser()
        if not skill_src.exists():
            return prompt

        raw = skill_src.read_text()
        # Strip YAML frontmatter
        body = re.sub(r"^---\n.*?\n---\n", "", raw, flags=re.DOTALL).strip()
        return f"[Skill context]\n{body}\n[End skill context]\n\n{prompt}"

    def _cli_available(self) -> bool:
        codex = self._codex_command()
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

        if not final_output and transcript:
            for turn in reversed(transcript):
                if turn.role == "assistant" and turn.content:
                    final_output = turn.content
                    break

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

    def _build_env(self, isolated_home: str, extra_path: list[str]) -> dict[str, str]:
        path = os.environ.get("PATH", "")
        if extra_path:
            path = os.pathsep.join(extra_path) + os.pathsep + path

        env = {
            "HOME": isolated_home,
            "PATH": path,
        }
        return self._passthrough(env, ("LANG", "LC_ALL", "TERM", "TMPDIR"))

    def _codex_command(self) -> str | None:
        configured = os.environ.get("CODEX_CLI_PATH")
        if configured and Path(configured).exists():
            return configured
        if CODEX_APP_CLI.exists():
            return str(CODEX_APP_CLI)
        return shutil.which("codex")

    # --- bare prompt call (the judge's half of the seam) -------------------

    def _prompt_command(
        self, prompt: str, model: str | None, extras: dict
    ) -> tuple[list[str], str | None, Callable[[], None] | None]:
        codex = self._codex_command()
        if not codex:
            raise HarnessConfigurationError("codex CLI not found")

        # `--output-last-message` writes the final answer to a file, which is
        # the only clean channel: codex's stdout is a noisy session log. The
        # file outlives the process so _prompt_output can read it; it is
        # deleted there, not via the post-exec cleanup hook.
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as output_file:
            extras["output_path"] = output_file.name

        cmd = [
            codex,
            "exec",
            "--skip-git-repo-check",
            "--dangerously-bypass-approvals-and-sandbox",
            "--color",
            "never",
            "--output-last-message",
            extras["output_path"],
            "-",
        ]
        if model:
            cmd[2:2] = ["--model", model]
        return cmd, prompt, None

    def _prompt_output(
        self, proc: ProcessResult, model: str | None, extras: dict
    ) -> PromptResult:
        output_path = Path(extras["output_path"])
        try:
            raw = output_path.read_text().strip() if output_path.exists() else ""
        finally:
            output_path.unlink(missing_ok=True)

        raw = raw or proc.stdout.strip()
        if proc.returncode != 0:
            detail = _extract_codex_error(proc.stderr) or _extract_codex_error(raw)
            return PromptResult(
                text=raw,
                resolved_model=model,
                error=detail or f"codex judge exited {proc.returncode}",
            )
        # Codex doesn't surface the resolved model in this mode, so we can only
        # report the one that was requested (None when its own default ran).
        return PromptResult(text=raw, resolved_model=model, error=None)

    def _copy_codex_config(self, ctx: RunContext) -> None:
        codex_home = Path(ctx.isolated_home) / ".codex"
        real_codex_home = Path.home() / ".codex"

        auth_src = real_codex_home / "auth.json"
        if auth_src.exists():
            codex_home.mkdir(parents=True, exist_ok=True)
            shutil.copy2(auth_src, codex_home / "auth.json")

        self._materialize_config(ctx, codex_home, real_codex_home / "config.toml")

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
        """
        base = ""
        real_exists = real_config.exists()
        if real_exists:
            base = self._strip_seeded_config(real_config.read_text())
        servers = self._translate_mcp_servers(ctx)

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

    def _strip_seeded_config(self, config: str) -> str:
        """Drop the top-level ``model =`` line and every ``[mcp_servers*]`` table.

        The model line is stripped so the seeded config never pins a model over the
        caliper invocation; the ``mcp_servers`` tables are stripped so the user's
        ambient personal servers are replaced (in ``_materialize_config``) by
        exactly the declared set. Line-based on purpose: it needs no TOML *reader*
        (unavailable on the 3.10 floor) and mirrors codex's own table layout.
        """
        filtered: list[str] = []
        in_table = False
        dropping_mcp = False
        for line in config.splitlines():
            stripped = line.strip()
            if stripped.startswith("["):
                in_table = True
                dropping_mcp = self._is_mcp_servers_header(stripped)
            if dropping_mcp:
                continue
            if not in_table and stripped.startswith("model ="):
                continue
            filtered.append(line)
        return "\n".join(filtered) + ("\n" if config.endswith("\n") else "")

    @staticmethod
    def _is_mcp_servers_header(stripped: str) -> bool:
        """True for a ``[mcp_servers]``/``[mcp_servers.x]``/``[[mcp_servers…]]`` header."""
        return (
            stripped == "[mcp_servers]"
            or stripped.startswith("[mcp_servers.")
            or stripped.startswith("[[mcp_servers")
        )

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
