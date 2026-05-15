from __future__ import annotations

import json
import os
import subprocess

from verdict.harness.base import ConversationTurn
from verdict.judge.base import Judge, JudgeResult
from verdict.judge.autorater import _format_transcript, _SYSTEM, _USER_TMPL
from verdict.schema.spec import JudgeConfig, TaskSpec


class ClaudeCodeJudge(Judge):
    """Judge that uses the `claude` CLI instead of the Anthropic SDK directly."""

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

        cmd = ["claude", "-p", prompt, "--output-format", "text"]
        if self._config.model:
            cmd += ["--model", self._config.model]

        env = dict(os.environ)

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=60,
                env=env,
            )
            raw = proc.stdout.strip()
        except subprocess.TimeoutExpired:
            return JudgeResult(
                passed=False,
                reasoning="Judge timed out.",
                autorater_passed=False,
                autorater_reasoning="Judge timed out.",
            )

        # Strip markdown fences if claude wrapped the JSON
        if raw.startswith("```"):
            lines = raw.splitlines()
            raw = "\n".join(
                line for line in lines if not line.startswith("```")
            ).strip()

        try:
            verdict = json.loads(raw)
            passed = bool(verdict.get("passed", False))
            reasoning = str(verdict.get("reasoning", ""))
        except (json.JSONDecodeError, KeyError):
            passed = False
            reasoning = f"Judge returned unparseable response: {raw[:200]}"

        return JudgeResult(
            passed=passed,
            reasoning=reasoning,
            autorater_passed=passed,
            autorater_reasoning=reasoning,
        )
