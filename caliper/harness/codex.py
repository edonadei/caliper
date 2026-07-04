from __future__ import annotations

import json
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Callable

from caliper.harness.base import (
    ConversationTurn,
    CliHarness,
    HarnessConfigurationError,
    ProcessResult,
    RunContext,
)
from caliper.schema.results import TokenUsage

CODEX_APP_CLI = Path("/Applications/Codex.app/Contents/Resources/codex")


class CodexHarness(CliHarness):
    def __init__(self, model: str | None = None) -> None:
        self._model = model

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
        self._copy_codex_config(ctx.isolated_home)

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

    def _copy_codex_config(self, isolated_home: str) -> None:
        home = Path(isolated_home)
        codex_home = home / ".codex"
        real_codex_home = Path.home() / ".codex"
        for filename in ("auth.json", "config.toml"):
            src = real_codex_home / filename
            if src.exists():
                codex_home.mkdir(parents=True, exist_ok=True)
                dst = codex_home / filename
                if filename == "config.toml":
                    dst.write_text(self._strip_top_level_model_config(src.read_text()))
                else:
                    shutil.copy2(src, dst)

    def _strip_top_level_model_config(self, config: str) -> str:
        filtered: list[str] = []
        in_table = False
        for line in config.splitlines():
            stripped = line.strip()
            if stripped.startswith("["):
                in_table = True
            if not in_table and stripped.startswith("model ="):
                continue
            filtered.append(line)
        return "\n".join(filtered) + ("\n" if config.endswith("\n") else "")

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
