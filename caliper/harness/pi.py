from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Callable

from caliper.harness.base import (
    ConversationTurn,
    CliHarness,
    HarnessConfigurationError,
    ProcessResult,
    RunContext,
)


class PiHarness(CliHarness):
    """CLI-subprocess backend for the `pi` coding agent.

    Drives the locally installed `pi` CLI in non-interactive JSON mode and
    loads the skill-under-test natively via pi's `--skill` flag (the
    agentskills.io standard), unlike codex which injects the skill into the
    prompt.
    """

    def __init__(self, model: str | None = None) -> None:
        self._model = model

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

    def _prepare(self, ctx: RunContext) -> None:
        # pi reads auth/settings from its config dir. We copy the real config
        # verbatim into a per-attempt directory (parallel-safe; never mutates
        # the user's real ~/.pi) and point pi at it via PI_CODING_AGENT_DIR.
        # The config's default model/provider is preserved on purpose; the
        # spec's `--model` overrides it when set. See issue #10 for why this
        # differs from codex (which strips its config default).
        ctx.extras["agent_dir"] = self._copy_pi_config(ctx.isolated_home)

    def _command(
        self, ctx: RunContext
    ) -> tuple[list[str], str | None, Callable[[], None] | None]:
        pi = self._pi_command() or "pi"
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
        if ctx.skill_path:
            # Prefer the staged copy in the run's cwd (siblings staged alongside,
            # cheat surfaces excluded) over the real skill dir; fall back to the
            # original path when nothing was staged (e.g. a lone command file).
            staged = Path(ctx.isolated_home) / "SKILL.md"
            skill_src = staged if staged.exists() else Path(ctx.skill_path).expanduser()
            if skill_src.exists():
                cmd += ["--skill", str(skill_src)]
        cmd.append(ctx.prompt)
        # stdin is left as None so the template closes it (DEVNULL): in --print
        # mode pi otherwise blocks reading stdin (e.g. for a trust confirmation)
        # and hangs until timeout.
        return cmd, None, None

    def _environment(self, ctx: RunContext) -> dict[str, str]:
        return self._build_env(
            ctx.isolated_home, ctx.extras["agent_dir"], ctx.extra_path
        )

    def _cli_available(self) -> bool:
        pi = self._pi_command()
        return pi is not None and self._version_ok(pi, timeout=10)

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

        if not final_output and transcript:
            for turn in reversed(transcript):
                if turn.role == "assistant" and turn.content:
                    final_output = turn.content
                    break

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

    def _build_env(
        self, isolated_home: str, agent_dir: Path, extra_path: list[str]
    ) -> dict[str, str]:
        path = os.environ.get("PATH", "")
        if extra_path:
            path = os.pathsep.join(extra_path) + os.pathsep + path

        env = {
            "HOME": isolated_home,
            "PATH": path,
            "PI_CODING_AGENT_DIR": str(agent_dir),
        }
        return self._passthrough(env, ("LANG", "LC_ALL", "TERM", "TMPDIR"))

    def _pi_command(self) -> str | None:
        configured = os.environ.get("PI_CLI_PATH")
        if configured and Path(configured).exists():
            return configured
        return shutil.which("pi")

    def _copy_pi_config(self, isolated_home: str) -> Path:
        agent_dir = Path(isolated_home) / ".pi" / "agent"
        real_agent_dir = Path.home() / ".pi" / "agent"
        for filename in ("auth.json", "settings.json"):
            src = real_agent_dir / filename
            if src.exists():
                agent_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, agent_dir / filename)
        return agent_dir

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
