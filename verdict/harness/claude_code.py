from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path

from verdict.harness.base import AttemptResult, ConversationTurn, HarnessBackend


class ClaudeCodeHarness(HarnessBackend):
    def __init__(self, model: str | None = None) -> None:
        self._model = model

    @property
    def name(self) -> str:
        return "claude"

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
        home = Path(isolated_home)
        commands_dir = home / ".claude" / "commands"
        commands_dir.mkdir(parents=True, exist_ok=True)

        # Copy auth files from the real HOME so the CLI finds its credentials.
        # Without this, the isolated HOME causes claude to fall back to
        # ANTHROPIC_API_KEY (which may be absent or unfunded).
        real_home = Path.home()
        for src, dst in [
            (real_home / ".claude.json", home / ".claude.json"),
            (real_home / ".claude" / ".credentials.json", home / ".claude" / ".credentials.json"),
        ]:
            if src.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)

        skill_file: Path | None = None
        if skill_path:
            skill_src = Path(skill_path).expanduser()
            skill_name = skill_src.stem
            uid = uuid.uuid4().hex[:8]
            skill_file = commands_dir / f"{skill_name}-vrd-{uid}.md"
            skill_file.write_text(skill_src.read_text())

        env = self._build_env(isolated_home, extra_path or [])
        cmd = self._build_cmd(prompt, model or self._model)

        start = time.monotonic()
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=env,
                cwd=str(home),
            )
        except subprocess.TimeoutExpired:
            return AttemptResult(
                task_id=task_id,
                attempt=attempt,
                transcript=[],
                final_output="",
                exit_code=124,
                duration_seconds=timeout,
                error="timeout",
            )
        finally:
            if skill_file and skill_file.exists():
                skill_file.unlink()

        duration = time.monotonic() - start
        transcript, final_output = self._parse_stream(proc.stdout)

        return AttemptResult(
            task_id=task_id,
            attempt=attempt,
            transcript=transcript,
            final_output=final_output,
            exit_code=proc.returncode,
            duration_seconds=duration,
            error=proc.stderr.strip() if proc.returncode != 0 and not final_output else None,
        )

    def _build_cmd(self, prompt: str, model: str | None) -> list[str]:
        cmd = [
            "claude",
            "-p", prompt,
            "--output-format", "stream-json",
            "--verbose",
            "--dangerously-skip-permissions",
        ]
        if model:
            cmd += ["--model", model]
        return cmd

    def _build_env(self, isolated_home: str, extra_path: list[str]) -> dict[str, str]:
        base_path = os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin")
        if extra_path:
            base_path = ":".join(extra_path) + ":" + base_path
        env = {
            "HOME": isolated_home,
            "PATH": base_path,
        }
        # Only forward ANTHROPIC_API_KEY if the caller explicitly set it AND
        # credentials.json is absent (i.e. the user is relying on the key, not
        # the claude OAuth session). Forwarding the key when credentials.json is
        # present would override stored OAuth auth with a potentially unfunded key.
        has_credentials = (Path.home() / ".claude" / ".credentials.json").exists()
        if not has_credentials:
            for key in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
                if key in os.environ:
                    env[key] = os.environ[key]
        return env

    def _parse_stream(self, stdout: str) -> tuple[list[ConversationTurn], str]:
        transcript: list[ConversationTurn] = []
        final_output = ""

        for line in stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue

            etype = event.get("type", "")

            if etype == "assistant":
                for block in event.get("message", {}).get("content", []):
                    btype = block.get("type", "")
                    if btype == "text":
                        transcript.append(ConversationTurn(role="assistant", content=block["text"]))
                    elif btype == "tool_use":
                        transcript.append(
                            ConversationTurn(
                                role="tool_use",
                                content=f"[tool: {block.get('name')}]",
                                tool_name=block.get("name"),
                                tool_input=block.get("input"),
                            )
                        )

            elif etype == "tool_result":
                content = event.get("content", "")
                if isinstance(content, list):
                    content = " ".join(
                        c.get("text", "") for c in content if isinstance(c, dict)
                    )
                transcript.append(
                    ConversationTurn(role="tool_result", content=content, tool_output=content)
                )

            elif etype == "result":
                final_output = event.get("result", "")

        if not final_output and transcript:
            for turn in reversed(transcript):
                if turn.role == "assistant" and turn.content:
                    final_output = turn.content
                    break

        return transcript, final_output
