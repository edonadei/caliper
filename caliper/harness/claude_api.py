from __future__ import annotations

import re
import time
from pathlib import Path

from caliper.harness.base import AttemptResult, ConversationTurn, HarnessBackend


class ClaudeAPIHarness(HarnessBackend):
    def __init__(self, model: str | None = None) -> None:
        self._model = model

    @property
    def name(self) -> str:
        return "claude-api"

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
        effective_model = model or self._model or "claude-haiku-4-5-20251001"
        start = time.monotonic()
        output, exit_code, error = self._run_api(full_prompt, effective_model, timeout)
        duration = time.monotonic() - start
        transcript = (
            [ConversationTurn(role="assistant", content=output)] if output else []
        )
        return AttemptResult(
            task_id=task_id,
            attempt=attempt,
            transcript=transcript,
            final_output=output,
            exit_code=exit_code,
            duration_seconds=duration,
            error=error,
            timed_out=exit_code == 124 and error == "timeout",
        )

    def _inject_skill(self, prompt: str, skill_path: str | None) -> str:
        if not skill_path:
            return prompt
        skill_src = Path(skill_path).expanduser()
        if not skill_src.exists():
            return prompt
        raw = skill_src.read_text()
        body = re.sub(r"^---\n.*?\n---\n", "", raw, flags=re.DOTALL).strip()
        return f"[Skill context]\n{body}\n[End skill context]\n\n{prompt}"

    def _run_api(
        self, prompt: str, model: str, timeout: int
    ) -> tuple[str, int, str | None]:
        try:
            import anthropic
        except ImportError:
            return "", 1, "anthropic package not installed; run: pip install caliper"

        try:
            client = anthropic.Anthropic(timeout=timeout)
            response = client.messages.create(
                model=model,
                max_tokens=4096,
                messages=[{"role": "user", "content": prompt}],
            )
            content = "".join(
                block.text
                for block in response.content
                if getattr(block, "type", None) == "text"
            )
            return content.strip(), 0, None
        except Exception as exc:
            if "Timeout" in type(exc).__name__:
                return "", 124, "timeout"
            return "", 1, str(exc)
