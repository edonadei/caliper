from __future__ import annotations

import json
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


class PiHarness(CliHarness):
    """CLI-subprocess backend for the `pi` coding agent.

    Drives the locally installed `pi` CLI in non-interactive JSON mode and
    loads the skill-under-test natively via pi's `--skill` flag (the
    agentskills.io standard), unlike codex which injects the skill into the
    prompt.
    """

    def __init__(self, model: str | None = None) -> None:
        self._model = model

    # pi has no MCP support, and this is a deliberate stance of the tool (its
    # README: "No MCP. Build a CLI tool with a README, or an extension that adds
    # MCP support."), not a slice caliper hasn't wired yet. So ``supports_mcp``
    # stays False and the refusal carries a permanent-by-design hint that points
    # the spec author at pi's own escape hatch rather than implying a later slice.
    mcp_unsupported_hint = (
        "The pi agent has no MCP support, and this is by design — pi will not "
        "honor an mcp: block natively. Instead of MCP, expose the same "
        "capability as a CLI tool the skill drives (a skill with a README), or "
        "build a pi extension that adds it. To measure MCP-based skills under "
        "caliper, run them on the claude-code backend (--model claude-code)."
    )

    cli_name = "pi"
    cli_path_env_var = "PI_CLI_PATH"

    @property
    def name(self) -> str:
        return "pi"

    def _ensure_ready(self, ctx: RunContext) -> None:
        if not self._cli_available():
            raise HarnessConfigurationError(
                "pi CLI is not available for the `pi` backend.\n\n"
                "Install the pi coding agent (`npm install -g "
                "@earendil-works/pi-coding-agent`) and authenticate it, or set "
                "`PI_CLI_PATH` to the pi binary, then rerun caliper."
            )

    def seed_files(self, ctx: RunContext) -> list[tuple[Path, Path]]:
        # pi reads auth/settings from its config dir. The real config is copied
        # verbatim into a per-attempt directory (parallel-safe; never mutates
        # the user's real ~/.pi) that PI_CODING_AGENT_DIR then points pi at.
        # The config's default model/provider is preserved on purpose; the
        # spec's `--model` overrides it when set. See issue #10 for why this
        # differs from codex (which strips its config default).
        real = Path.home() / ".pi" / "agent"
        agent_dir = self._agent_dir(ctx)
        return [
            (real / name, agent_dir / name) for name in ("auth.json", "settings.json")
        ]

    @staticmethod
    def _agent_dir(ctx: RunContext) -> Path:
        """The per-attempt config dir pi is pointed at via PI_CODING_AGENT_DIR.

        Derived from the isolated home rather than stashed in ``ctx.extras``:
        it is a function of the context, so there is nothing for a hook to
        remember between them.
        """
        return Path(ctx.isolated_home) / ".pi" / "agent"

    def skills_root(self, ctx: RunContext) -> Path:
        return self._agent_dir(ctx) / "skills"

    def _command(
        self, ctx: RunContext
    ) -> tuple[list[str], str | None, Callable[[], None] | None]:
        pi = self.cli_path() or "pi"
        cmd = [
            pi,
            "--print",
            "--mode",
            "json",
            "--no-session",
            # Trust the project-local skill file for this run; without it pi
            # blocks on an interactive trust prompt when a skill is loaded.
            "--approve",
        ]
        if ctx.model:
            cmd += ["--model", ctx.model]
        # No --skill: that flag preloads, and pi's own --no-skills exists
        # precisely because discovery is its default. The neighbourhood is
        # installed under the agent dir and pi finds it (docs/adr/0013).
        cmd.append(ctx.prompt)
        # stdin is left as None so the template closes it (DEVNULL): in --print
        # mode pi otherwise blocks reading stdin (e.g. for a trust confirmation)
        # and hangs until timeout.
        return cmd, None, None

    def _environment(self, ctx: RunContext) -> dict[str, str]:
        return self._isolated_env(
            ctx, extra={"PI_CODING_AGENT_DIR": str(self._agent_dir(ctx))}
        )

    def _cli_available(self) -> bool:
        pi = self.cli_path()
        return pi is not None and self._version_ok(pi, timeout=10)

    def _usage(self, proc: ProcessResult, ctx: RunContext) -> TokenUsage | None:
        """Sum per-assistant-message usage across the stream.

        pi reports usage on each ``message_end``; summing the assistant messages'
        usage (each carries its own turn's counts) gives the run total. pi's
        ``input`` is already non-cached, so ``cacheRead``/``cacheWrite`` map
        straight onto the disjoint cache fields.
        """
        totals = {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0}
        seen = False
        for line in proc.stdout.splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict) or event.get("type") != "message_end":
                continue
            message = event.get("message")
            if not isinstance(message, dict) or message.get("role") != "assistant":
                continue
            usage = message.get("usage")
            if not isinstance(usage, dict):
                continue
            seen = True
            for key in totals:
                value = usage.get(key)
                if isinstance(value, int):
                    totals[key] += value
        if not seen:
            return None
        return TokenUsage(
            input_tokens=totals["input"],
            output_tokens=totals["output"],
            cache_read_tokens=totals["cacheRead"],
            cache_creation_tokens=totals["cacheWrite"],
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

            etype = event.get("type")

            if etype == "tool_execution_start":
                tool_name = event.get("toolName")
                args = event.get("args")
                transcript.append(
                    ConversationTurn(
                        role="tool_use",
                        content=f"[tool: {tool_name}]",
                        tool_name=tool_name,
                        tool_input=args if isinstance(args, dict) else None,
                    )
                )
                continue

            if etype == "tool_execution_end":
                output = self._flatten_result(event.get("result"))
                transcript.append(
                    ConversationTurn(
                        role="tool_result",
                        content=output,
                        tool_output=output,
                    )
                )
                continue

            if etype == "message_end":
                message = event.get("message")
                if not isinstance(message, dict) or message.get("role") != "assistant":
                    continue
                text = self._flatten_text(message.get("content"))
                if text:
                    transcript.append(ConversationTurn(role="assistant", content=text))
                    final_output = text

        return transcript, final_output

    def _flatten_text(self, content: object) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = [
                block.get("text", "")
                for block in content
                if isinstance(block, dict) and block.get("type") == "text"
            ]
            return "".join(parts).strip()
        return ""

    def _flatten_result(self, result: object) -> str:
        if isinstance(result, dict):
            return self._flatten_text(result.get("content"))
        if isinstance(result, str):
            return result
        return ""

    # --- bare prompt call (the judge's half of the seam) -------------------

    def _prompt_command(
        self, prompt: str, model: str | None, extras: dict
    ) -> tuple[list[str], str | None, Callable[[], None] | None]:
        pi = self.cli_path()
        if not pi:
            raise HarnessConfigurationError("pi CLI not found")

        cmd = [pi, "--print", "--mode", "json", "--no-session", "--approve"]
        if model:
            cmd += ["--model", model]
        cmd.append(prompt)
        # stdin None → the template closes it (DEVNULL): in --print mode pi
        # otherwise blocks reading stdin and hangs until timeout.
        return cmd, None, None

    def _prompt_text(self, proc: ProcessResult) -> str:
        # The answer is the last assistant message of pi's JSON event stream —
        # the same stream an attempt run reads, tail and all.
        return self._parse_stream_with_tail(proc.stdout)[1]

    def _diagnose(self, proc: ProcessResult, final_output: str) -> str | None:
        if proc.returncode == 0:
            return None

        text = "\n".join(part for part in (proc.stdout, proc.stderr) if part).strip()
        if not text:
            return None
        lowered = text.lower()

        provider_markers = (
            "no api key",
            "no credentials",
            "missing api key",
            "set an api key",
            "no provider",
            "unknown provider",
        )
        if any(marker in lowered for marker in provider_markers) or (
            "provider" in lowered and "api key" in lowered
        ):
            return (
                "pi cannot run with the current provider/credential "
                "configuration.\n\n"
                "Caliper copies your `~/.pi/agent` auth and settings verbatim "
                "and passes a model to pi only when you select one. The pi CLI "
                "returned:\n"
                f"  {text}\n\n"
                "pi's built-in default provider is `google`, so running "
                "`--model pi` with no model (and no Google credentials) can fail "
                "here. Pass `--model pi:<model>` for a provider you are "
                "authenticated for, or configure pi's default provider with `pi` "
                "directly, then rerun caliper."
            )

        auth_markers = (
            "401",
            "unauthorized",
            "not logged in",
            "please login",
            "please run /login",
            "authentication",
            "invalid api key",
            "subscription",
        )
        if any(marker in lowered for marker in auth_markers):
            return (
                "pi cannot run with the current authentication "
                "configuration.\n\n"
                "Caliper drives the local pi CLI and reuses its `~/.pi/agent` "
                "credentials. The pi CLI returned:\n"
                f"  {text}\n\n"
                "Authenticate pi (e.g. `pi` then `/login`, or set the provider "
                "API key), verify `pi --print 'Reply OK'` works in your normal "
                "shell, then rerun caliper."
            )

        return None
