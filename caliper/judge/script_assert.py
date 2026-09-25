from __future__ import annotations

import json
from pathlib import Path

from caliper.harness import get_harness
from caliper.harness.base import ConversationTurn, HarnessConfigurationError
from caliper.harness.prompt_failure import PromptFailureKind, format_judge_failure
from caliper.judge.base import Judge, JudgeResult
from caliper.schema.spec import (
    DEFAULT_BACKEND,
    TaskSpec,
    assert_script_path,
    resolve_judge_model,
)
from caliper.workdir import AttemptWorkdir, StepPhase

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
The script runs with no extra imports beyond the standard library, in the \
directory the assistant worked in, so relative paths name the files it wrote.

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


def _strip_markdown_fence(raw: str) -> str:
    raw = raw.strip()
    if not raw.startswith("```"):
        return raw
    lines = raw.splitlines()
    return "\n".join(line for line in lines if not line.startswith("```")).strip()


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


def _run_inline_script(
    code: str, workdir: AttemptWorkdir, phase: StepPhase
) -> tuple[bool | None, str]:
    """Run an assertion in the attempt workdir, where the agent left its files.

    ``(None, evidence)`` when it timed out: a check that hung has no verdict,
    so it cannot count against the skill (docs/adr/0029).
    """
    step = workdir.run_python(phase, code)
    if step.timed_out_after is not None:
        return None, f"{phase} timed out after {step.timed_out_after}s"
    if step.ok:
        return True, ""
    # The tail, where a traceback names the assertion that failed.
    return False, step.output[-500:]


def _parse_rich_response(raw: str, workdir: AttemptWorkdir) -> tuple[bool, str, bool]:
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
        passed, evidence = _run_inline_script(code, workdir, "check")
        detail = f"{reasoning} | script: {'ok' if passed else evidence}"
        if passed is None:
            return False, detail, True
        return passed, detail, False

    return bool(verdict.get("passed", False)), reasoning, False


def _run_assert_from_task(
    task: TaskSpec, workdir: AttemptWorkdir
) -> tuple[bool | None, str] | None:
    """Run the static assert field from the task spec, if present."""
    if not task.assert_script:
        return None

    script_path = assert_script_path(task.assert_script, Path(workdir.spec_dir))
    if script_path is None:
        code = task.assert_script.strip()
    else:
        if not script_path.is_file():
            return False, f"assert script not found: {script_path}"
        code = script_path.read_text()

    return _run_inline_script(code, workdir, "assert")


class EvalJudge(Judge):
    """Universal judge: runs the static assert script and/or calls an LLM to evaluate."""

    def __init__(
        self, backend: str = DEFAULT_BACKEND, model: str | None = None
    ) -> None:
        # The judge engine is a runtime axis, resolved from --judge-model (ADR
        # 0004). ``model`` stays as *requested*: ``None`` means the pinned
        # default is applied at call time, and a run that never calls an
        # autorater (assert-only) records no judge model rather than one that
        # never ran.
        self.backend = backend
        self.model = model

    def evaluate(
        self,
        task: TaskSpec,
        transcript: list[ConversationTurn],
        final_output: str,
        workdir: AttemptWorkdir,
    ) -> JudgeResult:
        assert_passed: bool | None = None
        assert_evidence: str | None = None
        autorater_passed: bool | None = None
        autorater_reasoning: str | None = None
        autorater_errored = False

        static_result = _run_assert_from_task(task, workdir)
        if static_result is not None:
            assert_passed, assert_evidence = static_result

        autorater_model: str | None = None
        if task.expect:
            (
                llm_passed,
                llm_reasoning,
                autorater_errored,
                autorater_model,
            ) = self._llm_evaluate(task, transcript, workdir)
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
            resolved_model=autorater_model,
        )

    def _llm_evaluate(
        self,
        task: TaskSpec,
        transcript: list[ConversationTurn],
        workdir: AttemptWorkdir,
    ) -> tuple[bool, str, bool, str | None]:
        # An autorater is one bare prompt through a CLI agent — the same
        # backend adapters that run attempts also answer the judge, via the
        # ``run_prompt`` half of the backend seam.
        try:
            harness = get_harness(
                self.backend, resolve_judge_model(self.backend, self.model)
            )
        except ValueError:
            return False, f"Unknown judge backend: {self.backend!r}", True, None

        user_msg = _USER_TMPL.format(
            expect=task.expect,
            transcript=_format_transcript(transcript),
        )
        prompt = f"{_SYSTEM}\n\n{user_msg}"

        # In the workdir, not the spec dir: the judge grades what the agent left
        # there, and must not sit beside the answer key or write into the
        # author's repo (docs/adr/0026).
        result = harness.run_prompt(prompt, cwd=workdir.path, timeout=60)
        if result.failure is not None:
            # Switch on the typed kind here, in the judge — provider status codes
            # never leak past the harness boundary (issue #75, ADR-0001).
            reasoning = format_judge_failure(result.failure, result.resolved_model)
            if result.failure.kind is PromptFailureKind.MODEL_UNAVAILABLE:
                # The same model fails every attempt's judge the same way, so a
                # per-attempt judge_error would pay for each agent run only to
                # discard it. Stop the run instead (issue #139).
                raise HarnessConfigurationError(reasoning)
            return False, reasoning, True, result.resolved_model
        if result.error:
            return False, result.error, True, result.resolved_model
        passed, reasoning, errored = _parse_rich_response(
            _strip_markdown_fence(result.text), workdir
        )
        return passed, reasoning, errored, result.resolved_model
