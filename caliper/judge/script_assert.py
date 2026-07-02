from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

from caliper.harness.base import ConversationTurn
from caliper.judge.base import Judge, JudgeResult
from caliper.schema.spec import normalize_backend
from caliper.schema.spec import JudgeConfig, TaskSpec

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


def _parse_rich_response(raw: str, spec_dir: str) -> tuple[bool, str, bool]:
    """Parse an autorater response into (passed, reasoning, errored).

    ``errored`` is True when the autorater failed to yield a usable verdict at
    all (unparseable JSON, or a malformed verdict object). It is distinct from a
    verdict of ``passed=False`` — a real judgment that the task failed.
    """
    try:
        verdict = json.loads(raw)
    except json.JSONDecodeError:
        return False, f"Judge returned unparseable response: {raw[:200]}", True

    mode = verdict.get("mode", "verdict")
    reasoning = str(verdict.get("reasoning", ""))

    if mode == "script":
        code = verdict.get("code", "")
        if not code:
            return False, "Judge returned empty script", True
        passed, evidence = _run_inline_script(code, spec_dir)
        detail = f"{reasoning} | script: {'ok' if passed else evidence}"
        return passed, detail, False

    return bool(verdict.get("passed", False)), reasoning, False


def _run_assert_from_task(task: TaskSpec, spec_dir: str) -> tuple[bool, str] | None:
    """Run the static assert field from the task spec, if present."""
    if not task.assert_script:
        return None

    raw = task.assert_script.strip()
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


class EvalJudge(Judge):
    """Universal judge: runs the static assert script and/or calls an LLM to evaluate."""

    strategy = "script"

    def __init__(self, config: JudgeConfig) -> None:
        self._config = config
        self._client = None

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
        autorater_errored = False

        static_result = _run_assert_from_task(task, spec_dir)
        if static_result is not None:
            assert_passed, assert_evidence = static_result

        if task.expect:
            llm_passed, llm_reasoning, autorater_errored = self._llm_evaluate(
                task, transcript, spec_dir
            )
            autorater_reasoning = llm_reasoning
            # An errored autorater yields no verdict: leave autorater_passed None
            # so it is dropped from the checks rather than counted as a failure.
            autorater_passed = None if autorater_errored else llm_passed

        # Rule B: only checks that produced a verdict count. A judge_error is
        # raised only when *no* verdict survives (see ADR-0001).
        checks = [c for c in (assert_passed, autorater_passed) if c is not None]
        errored = not checks
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
            errored=errored,
        )

    def _llm_evaluate(
        self, task: TaskSpec, transcript: list[ConversationTurn], spec_dir: str
    ) -> tuple[bool, str, bool]:
        match normalize_backend(self._config.backend):
            case "codex":
                from caliper.judge.codex_judge import evaluate_with_codex

                return evaluate_with_codex(
                    expect=task.expect,
                    transcript=transcript,
                    model=self._config.model,
                    cwd=spec_dir,
                )
            case "claude-code":
                from caliper.judge.claude_code_judge import evaluate_with_claude_code

                return evaluate_with_claude_code(
                    expect=task.expect,
                    transcript=transcript,
                    model=self._config.model,
                    spec_dir=spec_dir,
                )
            case "openai-api":
                from caliper.judge.openai_api_judge import evaluate_with_openai_api

                return evaluate_with_openai_api(
                    expect=task.expect,
                    transcript=transcript,
                    model=self._config.model,
                    spec_dir=spec_dir,
                )
            case "claude-api":
                model = self._config.model or "claude-haiku-4-5-20251001"
                if self._client is None:
                    import anthropic

                    self._client = anthropic.Anthropic()
                user_msg = _USER_TMPL.format(
                    expect=task.expect,
                    transcript=_format_transcript(transcript),
                )
                try:
                    response = self._client.messages.create(
                        model=model,
                        max_tokens=1024,
                        system=_SYSTEM,
                        messages=[{"role": "user", "content": user_msg}],
                    )
                    raw = response.content[0].text.strip()
                except Exception as exc:
                    return False, f"claude-api judge failed: {exc}", True
                return _parse_rich_response(raw, spec_dir)
            case _:
                return False, f"Unknown judge backend: {self._config.backend!r}", True
