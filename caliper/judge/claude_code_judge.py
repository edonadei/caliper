from __future__ import annotations

import json
import os
import subprocess

from caliper.harness.base import ConversationTurn
from caliper.judge.base import Judge, JudgeResult
from caliper.judge.autorater import _format_transcript, _SYSTEM, _USER_TMPL
from caliper.schema.spec import JudgeConfig, TaskSpec


class ClaudeCodeJudge(Judge):
    """Judge that uses the `claude` CLI instead of the Anthropic SDK directly."""

    strategy = "claude-code"

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

        user_msg = _USER_TMPL.format(
            expect=task.expect,
            transcript=_format_transcript(transcript),
        )
        prompt = f"{_SYSTEM}\n\n{user_msg}"

        passed, reasoning = evaluate_prompt_with_claude_code(prompt, self._config.model)

        return JudgeResult(
            passed=passed,
            reasoning=reasoning,
            autorater_passed=passed,
            autorater_reasoning=reasoning,
        )


def evaluate_with_claude_code(
    *,
    expect: str,
    transcript: list[ConversationTurn],
    model: str | None,
) -> tuple[bool, str]:
    user_msg = _USER_TMPL.format(
        expect=expect,
        transcript=_format_transcript(transcript),
    )
    prompt = f"{_SYSTEM}\n\n{user_msg}"
    return evaluate_prompt_with_claude_code(prompt, model)


def evaluate_prompt_with_claude_code(prompt: str, model: str | None) -> tuple[bool, str]:
    cmd = ["claude", "-p", prompt, "--output-format", "text"]
    if model:
        cmd += ["--model", model]

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60,
            env=dict(os.environ),
        )
        raw = proc.stdout.strip()
    except subprocess.TimeoutExpired:
        return False, "Judge timed out."

    if raw.startswith("```"):
        lines = raw.splitlines()
        raw = "\n".join(line for line in lines if not line.startswith("```")).strip()

    try:
        verdict = json.loads(raw)
        passed = bool(verdict.get("passed", False))
        reasoning = str(verdict.get("reasoning", ""))
    except (json.JSONDecodeError, KeyError):
        passed = False
        reasoning = f"Judge returned unparseable response: {raw[:200]}"

    return passed, reasoning
