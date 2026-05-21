from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path

from verdict.harness.base import AttemptResult, ConversationTurn, HarnessBackend


class CodexHarness(HarnessBackend):
    def __init__(self, model: str | None = None) -> None:
        self._model = model

    @property
    def name(self) -> str:
        return "codex"

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
        full_prompt = self._inject_skill(prompt, skill_path)
        effective_model = model or self._model or "codex-mini-latest"
        start = time.monotonic()

        if self._cli_available():
            output, exit_code, error = self._run_cli(
                full_prompt,
                effective_model,
                timeout,
                isolated_home,
                extra_path or [],
            )
        else:
            output, exit_code, error = self._run_api(full_prompt, effective_model, timeout)

        duration = time.monotonic() - start
        transcript = [ConversationTurn(role="assistant", content=output)] if output else []

        return AttemptResult(
            task_id=task_id,
            attempt=attempt,
            transcript=transcript,
            final_output=output,
            exit_code=exit_code,
            duration_seconds=duration,
            error=error,
        )

    def _inject_skill(self, prompt: str, skill_path: str | None) -> str:
        if not skill_path:
            return prompt
        import re

        skill_src = Path(skill_path).expanduser()
        if not skill_src.exists():
            return prompt

        raw = skill_src.read_text()
        # Strip YAML frontmatter
        body = re.sub(r"^---\n.*?\n---\n", "", raw, flags=re.DOTALL).strip()
        return f"[Skill context]\n{body}\n[End skill context]\n\n{prompt}"

    def _cli_available(self) -> bool:
        codex = shutil.which("codex")
        if codex is None:
            return False
        try:
            result = subprocess.run(
                [codex, "--version"],
                capture_output=True,
                text=True,
                timeout=5,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        return result.returncode == 0

    def _run_cli(
        self,
        prompt: str,
        model: str,
        timeout: int,
        isolated_home: str,
        extra_path: list[str],
    ) -> tuple[str, int, str | None]:
        codex = shutil.which("codex") or "codex"
        env = self._build_env(isolated_home, extra_path)
        try:
            result = subprocess.run(
                [
                    codex,
                    "exec",
                    "--model",
                    model,
                    "--skip-git-repo-check",
                    "--dangerously-bypass-approvals-and-sandbox",
                    "--color",
                    "never",
                    "-",
                ],
                input=prompt,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=env,
                cwd=isolated_home,
            )
            return result.stdout.strip(), result.returncode, result.stderr.strip() or None
        except subprocess.TimeoutExpired:
            return "", 124, "timeout"
        except OSError as exc:
            return "", 1, f"codex CLI failed: {exc}"

    def _build_env(self, isolated_home: str, extra_path: list[str]) -> dict[str, str]:
        import os

        path = os.environ.get("PATH", "")
        if extra_path:
            path = os.pathsep.join(extra_path) + os.pathsep + path

        env = os.environ.copy()
        env["HOME"] = isolated_home
        env["PATH"] = path
        return env

    def _run_api(self, prompt: str, model: str, timeout: int) -> tuple[str, int, str | None]:
        try:
            from openai import OpenAI
        except ImportError:
            return "", 1, "openai package not installed; run: pip install verdict-eval[codex]"

        try:
            client = OpenAI()
            response = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                timeout=timeout,
            )
            content = response.choices[0].message.content or ""
            return content.strip(), 0, None
        except Exception as exc:
            return "", 1, str(exc)
