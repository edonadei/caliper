from __future__ import annotations

import os
import shutil
import subprocess
import time

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
    ) -> AttemptResult:
        full_prompt = self._inject_skill(prompt, skill_path)
        effective_model = model or self._model or "codex-mini-latest"
        start = time.monotonic()

        if self._cli_available():
            output, exit_code, error = self._run_cli(full_prompt, timeout)
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
        from pathlib import Path
        import re

        skill_src = Path(skill_path).expanduser()
        if not skill_src.exists():
            return prompt

        raw = skill_src.read_text()
        # Strip YAML frontmatter
        body = re.sub(r"^---\n.*?\n---\n", "", raw, flags=re.DOTALL).strip()
        return f"[Skill context]\n{body}\n[End skill context]\n\n{prompt}"

    def _cli_available(self) -> bool:
        return shutil.which("codex") is not None

    def _run_cli(self, prompt: str, timeout: int) -> tuple[str, int, str | None]:
        try:
            result = subprocess.run(
                ["codex", "--quiet", prompt],
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            return result.stdout.strip(), result.returncode, result.stderr.strip() or None
        except subprocess.TimeoutExpired:
            return "", 124, "timeout"
        except FileNotFoundError:
            return "", 1, "codex CLI not found"

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
