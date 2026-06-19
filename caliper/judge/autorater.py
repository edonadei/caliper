from __future__ import annotations

import json

import anthropic

from caliper.harness.base import ConversationTurn
from caliper.judge.base import Judge, JudgeResult
from caliper.schema.spec import JudgeConfig, TaskSpec

_SYSTEM = """\
You are an evaluation judge for an AI assistant. You will be shown a conversation \
transcript (including all tool calls and their results) produced by the AI, along with \
an expectation that describes what a successful run looks like.

Your job: decide whether the transcript satisfies the expectation.
Respond with valid JSON only — no markdown fences, no extra text.
Format: {"passed": true|false, "reasoning": "<one or two sentences>"}
"""

_USER_TMPL = """\
<expectation>
{expect}
</expectation>

<transcript>
{transcript}
</transcript>

Does the transcript satisfy the expectation? Respond with JSON.
"""


def _format_transcript(turns: list[ConversationTurn]) -> str:
    lines: list[str] = []
    for t in turns:
        if t.role == "assistant":
            lines.append(f"[assistant] {t.content}")
        elif t.role == "tool_use":
            inp = json.dumps(t.tool_input, ensure_ascii=False) if t.tool_input else ""
            lines.append(f"[tool_use: {t.tool_name}] {inp}")
        elif t.role == "tool_result":
            out = (t.tool_output or "")[:2000]
            lines.append(f"[tool_result] {out}")
    return "\n".join(lines) or "(empty transcript)"


class AutoraterJudge(Judge):
    strategy = "autorater"

    def __init__(self, config: JudgeConfig) -> None:
        self._config = config
        self._client = anthropic.Anthropic()

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

        model = self._config.model or "claude-haiku-4-5-20251001"
        response = self._client.messages.create(
            model=model,
            max_tokens=512,
            system=_SYSTEM,
            messages=[{"role": "user", "content": user_msg}],
        )

        raw = response.content[0].text.strip()
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


def evaluate_with_claude_api(
    *,
    expect: str,
    transcript: list[ConversationTurn],
    model: str | None,
    timeout: int = 60,
) -> tuple[bool, str]:
    user_msg = _USER_TMPL.format(
        expect=expect,
        transcript=_format_transcript(transcript),
    )

    try:
        client = anthropic.Anthropic(timeout=timeout)
        response = client.messages.create(
            model=model or "claude-haiku-4-5-20251001",
            max_tokens=512,
            system=_SYSTEM,
            messages=[{"role": "user", "content": user_msg}],
        )
        raw = response.content[0].text.strip()
    except Exception as exc:
        return False, f"claude-api judge failed: {exc}"

    try:
        verdict = json.loads(raw)
        passed = bool(verdict.get("passed", False))
        reasoning = str(verdict.get("reasoning", ""))
    except (json.JSONDecodeError, KeyError):
        passed = False
        reasoning = f"Judge returned unparseable response: {raw[:200]}"

    return passed, reasoning
