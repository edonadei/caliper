from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

from caliper.harness.base import (
    AttemptResult,
    ConversationTurn,
    HarnessBackend,
    HarnessConfigurationError,
)


class PiHarness(HarnessBackend):
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

    def run(
        self,
        task_id: str,
        attempt: int,
        prompt: str,
        *,
        skill_path: str | None,
        model: str | None,
        timeout: int,
        isolated_home: str,
        extra_path: list[str] | None = None,
    ) -> AttemptResult:
        effective_model = model or self._model
        start = time.monotonic()

        if not self._cli_available():
            raise HarnessConfigurationError(
                "pi CLI is not available for the `pi` backend.\n\n"
                "Install the pi coding agent (`npm install -g "
                "@earendil-works/pi-coding-agent`) and authenticate it, or set "
                "`PI_CLI_PATH` to the pi binary, then rerun caliper."
            )

        # pi reads auth/settings from its config dir. We copy the real config
        # verbatim into a per-attempt directory (parallel-safe; never mutates
        # the user's real ~/.pi) and point pi at it via PI_CODING_AGENT_DIR.
        # The config's default model/provider is preserved on purpose; the
        # spec's `--model` overrides it when set. See issue #10 for why this
        # differs from codex (which strips its config default).
        agent_dir = self._copy_pi_config(isolated_home)

        output, exit_code, error = self._run_cli(
            prompt,
            effective_model,
            skill_path,
            timeout,
            isolated_home,
            agent_dir,
            extra_path or [],
        )
        diagnostic = self._diagnose_configuration_error(exit_code, output, error)
        if diagnostic:
            raise HarnessConfigurationError(diagnostic)

        duration = time.monotonic() - start
        transcript, final_output = self._parse_json_stream(output)
        if not transcript and output:
            transcript = [ConversationTurn(role="assistant", content=output)]
            final_output = output

        return AttemptResult(
            task_id=task_id,
            attempt=attempt,
            transcript=transcript,
            final_output=final_output,
            exit_code=exit_code,
            duration_seconds=duration,
            error=error,
        )

    def _cli_available(self) -> bool:
        pi = self._pi_command()
        if pi is None:
            return False
        try:
            result = subprocess.run(
                [pi, "--version"],
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        return result.returncode == 0

    def _run_cli(
        self,
        prompt: str,
        model: str | None,
        skill_path: str | None,
        timeout: int,
        isolated_home: str,
        agent_dir: Path,
        extra_path: list[str],
    ) -> tuple[str, int, str | None]:
        pi = self._pi_command() or "pi"
        env = self._build_env(isolated_home, agent_dir, extra_path)
        cmd = [
            pi,
            "--print",
            "--mode", "json",
            "--no-session",
            # Trust the project-local skill file for this run; without it pi
            # blocks on an interactive trust prompt when a skill is loaded.
            "--approve",
        ]
        if model:
            cmd += ["--model", model]
        if skill_path:
            # Prefer the staged copy in the run's cwd (siblings staged alongside,
            # cheat surfaces excluded) over the real skill dir; fall back to the
            # original path when nothing was staged (e.g. a lone command file).
            staged = Path(isolated_home) / "SKILL.md"
            skill_src = staged if staged.exists() else Path(skill_path).expanduser()
            if skill_src.exists():
                cmd += ["--skill", str(skill_src)]
        cmd.append(prompt)

        try:
            result = subprocess.run(
                cmd,
                # Close stdin: in --print mode pi otherwise blocks reading
                # stdin (e.g. for a trust confirmation) and hangs until timeout.
                stdin=subprocess.DEVNULL,
                capture_output=True,
                encoding="utf-8",
                text=True,
                timeout=timeout,
                env=env,
                cwd=isolated_home,
            )
            return result.stdout.strip(), result.returncode, result.stderr.strip() or None
        except subprocess.TimeoutExpired:
            return "", 124, "timeout"
        except OSError as exc:
            return "", 1, f"pi CLI failed: {exc}"

    def _parse_json_stream(self, stdout: str) -> tuple[list[ConversationTurn], str]:
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
        for key in ("LANG", "LC_ALL", "TERM", "TMPDIR"):
            if key in os.environ:
                env[key] = os.environ[key]
        return env

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

    def _diagnose_configuration_error(
        self,
        returncode: int,
        stdout: str,
        stderr: str | None,
    ) -> str | None:
        if returncode == 0:
            return None

        text = "\n".join(part for part in (stdout, stderr or "") if part).strip()
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
                "and passes `--model` only when the spec sets one. The pi CLI "
                "returned:\n"
                f"  {text}\n\n"
                "pi's built-in default provider is `google`, so a spec with no "
                "`model:` (and no Google credentials) can fail here. Set a "
                "`model:`/provider you are authenticated for in the spec, or "
                "configure pi's default provider with `pi` directly, then rerun "
                "caliper."
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
