from __future__ import annotations

import time

from caliper.harness.base import AttemptResult, ConversationTurn, HarnessBackend
from caliper.harness.codex import CodexHarness


class OpenAIAPIHarness(HarnessBackend):
    def __init__(self, model: str | None = None) -> None:
        self._model = model

    @property
    def name(self) -> str:
        return "openai-api"

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
        full_prompt = CodexHarness()._inject_skill(prompt, skill_path)
        effective_model = model or self._model or "gpt-4o-mini"
        start = time.monotonic()
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
            timed_out=exit_code == 124 and error == "timeout",
        )

    def _run_api(self, prompt: str, model: str, timeout: int) -> tuple[str, int, str | None]:
        try:
            from openai import OpenAI
        except ImportError:
            return "", 1, "openai package not installed; run: pip install caliper[openai]"

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
            if "Timeout" in type(exc).__name__:
                return "", 124, "timeout"
            return "", 1, str(exc)
