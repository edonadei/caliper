from __future__ import annotations

import json

from caliper.harness.base import ConversationTurn
from caliper.judge.autorater import _format_transcript, _SYSTEM, _USER_TMPL
from caliper.judge.base import Judge, JudgeResult
from caliper.schema.spec import JudgeConfig, TaskSpec


class OpenAIAPIJudge(Judge):
    """Judge that uses the OpenAI API explicitly."""

    def __init__(self, config: JudgeConfig) -> None:
        self._config = config

    def evaluate(
        self,
        task: TaskSpec,
        transcript: list[ConversationTurn],
        final_output: str,
        spec_dir: str,
    ) -> JudgeResult:
        if not task.expect:
            return JudgeResult(
                passed=True,
                reasoning="No expect defined; autorater skipped.",
                autorater_passed=None,
            )

        passed, reasoning = evaluate_with_openai_api(
            expect=task.expect,
            transcript=transcript,
            model=self._config.model,
        )
        return JudgeResult(
            passed=passed,
            reasoning=reasoning,
            autorater_passed=passed,
            autorater_reasoning=reasoning,
        )


def evaluate_with_openai_api(
    *,
    expect: str,
    transcript: list[ConversationTurn],
    model: str | None,
    timeout: int = 60,
) -> tuple[bool, str]:
    try:
        from openai import OpenAI
    except ImportError:
        return False, "openai package not installed; run: pip install caliper[openai]"

    user_msg = _USER_TMPL.format(
        expect=expect,
        transcript=_format_transcript(transcript),
    )
    try:
        client = OpenAI()
        response = client.chat.completions.create(
            model=model or "gpt-4o-mini",
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": user_msg},
            ],
            timeout=timeout,
        )
        raw = response.choices[0].message.content or ""
    except Exception as exc:
        return False, f"openai-api judge failed: {exc}"

    try:
        verdict = json.loads(_strip_markdown_fence(raw.strip()))
        passed = bool(verdict.get("passed", False))
        reasoning = str(verdict.get("reasoning", ""))
    except (json.JSONDecodeError, KeyError):
        passed = False
        reasoning = f"Judge returned unparseable response: {raw[:200]}"

    return passed, reasoning


def _strip_markdown_fence(raw: str) -> str:
    if not raw.startswith("```"):
        return raw
    lines = raw.splitlines()
    return "\n".join(line for line in lines if not line.startswith("```")).strip()
