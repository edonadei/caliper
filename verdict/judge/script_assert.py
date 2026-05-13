from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import anthropic

from verdict.harness.base import ConversationTurn
from verdict.judge.autorater import _format_transcript
from verdict.judge.base import Judge, JudgeResult
from verdict.schema.spec import JudgeConfig, TaskSpec

_SYSTEM = """\
You are an evaluation judge for an AI assistant. You will be shown a conversation \
transcript and an expectation describing what success looks like.

You have two response modes:

1. **Direct verdict** — if you can tell from the transcript alone whether the expectation \
was met, respond with:
   {"mode": "verdict", "passed": true|false, "reasoning": "<one or two sentences>"}

2. **Assertion script** — if verifiable facts (files exist, output matches a pattern, \
a value equals something) would make your judgment more reliable, write a Python script \
that asserts those facts and respond with:
   {"mode": "script", "code": "<python script>", "reasoning": "<why you chose this>"}

The script must use `assert` statements. `assert` failure = task failed. \
The script runs with no extra imports beyond the standard library.

Respond with valid JSON only — no markdown fences, no extra text.
"""

_USER_TMPL = """\
<expectation>
{expect}
</expectation>

<transcript>
{transcript}
</transcript>

Evaluate the transcript. Respond with JSON.
"""


def _run_inline_script(code: str, spec_dir: str) -> tuple[bool, str]:
    with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
        f.write(code)
        tmp_path = f.name
    try:
        result = subprocess.run(
            [sys.executable, tmp_path],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=spec_dir,
        )
        if result.returncode == 0:
            return True, ""
        evidence = (result.stderr or result.stdout).strip()
        return False, evidence[:500]
    except subprocess.TimeoutExpired:
        return False, "assertion script timed out"
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def _run_assert_from_task(task: TaskSpec, spec_dir: str) -> tuple[bool, str] | None:
    """Run the static assert field from the task spec, if present."""
    if not task.assert_script:
        return None

    raw = task.assert_script.strip()
    # File path: single line ending in .py, no newlines
    if "\n" not in raw and raw.endswith(".py"):
        script_path = Path(raw)
        if not script_path.is_absolute():
            script_path = Path(spec_dir) / script_path
        if not script_path.exists():
            return False, f"assert script not found: {script_path}"
        code = script_path.read_text()
    else:
        code = raw

    return _run_inline_script(code, spec_dir)


class ScriptAssertJudge(Judge):
    """
    Handles both the static task.assert field and LLM-generated assertion scripts.
    When task.expect is present, asks the LLM to either give a direct verdict or
    write a Python assertion script.
    """

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
        assert_passed: bool | None = None
        assert_evidence: str | None = None
        autorater_passed: bool | None = None
        autorater_reasoning: str | None = None

        # 1. Run the static assert from the spec (if any)
        static_result = _run_assert_from_task(task, spec_dir)
        if static_result is not None:
            assert_passed, assert_evidence = static_result

        # 2. Run the LLM judge (direct verdict or generated script)
        if task.expect:
            llm_passed, llm_reasoning = self._llm_evaluate(task, transcript, spec_dir)
            autorater_passed = llm_passed
            autorater_reasoning = llm_reasoning

        # Overall: both checks must pass (whichever are defined)
        checks = []
        if assert_passed is not None:
            checks.append(assert_passed)
        if autorater_passed is not None:
            checks.append(autorater_passed)

        overall = all(checks) if checks else False
        reasoning_parts = []
        if autorater_reasoning:
            reasoning_parts.append(autorater_reasoning)
        if assert_evidence:
            reasoning_parts.append(f"assert: {assert_evidence}")
        reasoning = " | ".join(reasoning_parts) or "no checks defined"

        return JudgeResult(
            passed=overall,
            reasoning=reasoning,
            assert_passed=assert_passed,
            assert_evidence=assert_evidence,
            autorater_passed=autorater_passed,
            autorater_reasoning=autorater_reasoning,
        )

    def _llm_evaluate(
        self, task: TaskSpec, transcript: list[ConversationTurn], spec_dir: str
    ) -> tuple[bool, str]:
        user_msg = _USER_TMPL.format(
            expect=task.expect,
            transcript=_format_transcript(transcript),
        )
        model = self._config.model or "claude-haiku-4-5-20251001"
        response = self._client.messages.create(
            model=model,
            max_tokens=1024,
            system=_SYSTEM,
            messages=[{"role": "user", "content": user_msg}],
        )
        raw = response.content[0].text.strip()

        try:
            verdict = json.loads(raw)
        except json.JSONDecodeError:
            return False, f"Judge returned unparseable response: {raw[:200]}"

        mode = verdict.get("mode", "verdict")
        reasoning = str(verdict.get("reasoning", ""))

        if mode == "script":
            code = verdict.get("code", "")
            if not code:
                return False, "Judge returned empty script"
            passed, evidence = _run_inline_script(code, spec_dir)
            detail = f"{reasoning} | script: {'ok' if passed else evidence}"
            return passed, detail

        # mode == "verdict"
        return bool(verdict.get("passed", False)), reasoning
