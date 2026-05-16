from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
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

        # On macOS, OAuth credentials may live in the Keychain rather than in
        # .credentials.json. Seed the isolated home so the subprocess can auth
        # without a browser login flow.
        creds_dst = home / ".claude" / ".credentials.json"
        if sys.platform == "darwin" and not creds_dst.exists():
            self._seed_credentials_from_keychain(creds_dst)

        has_file_credentials = creds_dst.exists()

        skill_file: Path | None = None
        if skill_path:
            skill_src = Path(skill_path).expanduser()
            skill_name = skill_src.stem
            uid = uuid.uuid4().hex[:8]
            skill_file = commands_dir / f"{skill_name}-vrd-{uid}.md"
            skill_file.write_text(skill_src.read_text())

        env = self._build_env(isolated_home, extra_path or [], has_file_credentials)
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

    def _seed_credentials_from_keychain(self, dst: Path) -> None:
        try:
            result = subprocess.run(
                ["security", "find-generic-password", "-s", "Claude Code-credentials", "-w"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0 and result.stdout.strip():
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_text(result.stdout.strip())
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass

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

    def _build_env(
        self, isolated_home: str, extra_path: list[str], has_file_credentials: bool = False
    ) -> dict[str, str]:
        base_path = os.environ.get("PATH", "")

        # On macOS, IDE-launched processes often have a stripped PATH that
        # omits Homebrew prefixes. Prepend them when present so tools installed
        # via Homebrew (e.g. on Apple Silicon at /opt/homebrew/bin) are found.
        if sys.platform == "darwin":
            homebrew_candidates = ["/opt/homebrew/bin", "/opt/homebrew/sbin", "/usr/local/bin"]
            existing = set(base_path.split(os.pathsep))
            additions = [p for p in homebrew_candidates if os.path.isdir(p) and p not in existing]
            if additions:
                base_path = os.pathsep.join(additions) + os.pathsep + base_path

        if extra_path:
            base_path = os.pathsep.join(extra_path) + os.pathsep + base_path

        env: dict[str, str] = {
            "HOME": isolated_home,
            "PATH": base_path,
        }

        # macOS uses TMPDIR for the per-user secure temp directory; Node.js
        # (and therefore the claude CLI) reads it to locate scratch space.
        if sys.platform == "darwin" and "TMPDIR" in os.environ:
            env["TMPDIR"] = os.environ["TMPDIR"]

        # Only forward API keys when there are no file-based credentials and
        # no Keychain credentials — avoids overriding valid OAuth auth with a
        # potentially unfunded key.
        if not has_file_credentials:
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
