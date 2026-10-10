from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from caliper.backends import DEFAULT_BACKEND
from caliper.harness import get_harness
from caliper.harness.base import ConversationTurn
from caliper.judge.base import Judge, JudgeResult, PromptBackend
from caliper.schema.results import TranscriptTurn
from caliper.schema.spec import (
    TaskSpec,
    assert_script_path,
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


def _format_transcript(turns: Sequence[ConversationTurn | TranscriptTurn]) -> str:
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


def render_judge_prompt(
    expect: str, transcript: Sequence[ConversationTurn | TranscriptTurn]
) -> str:
    """The exact prompt the autorater is sent for one attempt.

    Public so a saved run's judge input can be rebuilt from what the run already
    stores — ``TaskResult.expect`` and ``AttemptRecord.transcript`` — rather
    than saving the prompt a second time. Faithful only while
    ``JUDGE_PROMPT_VERSION`` matches the run's ``judge_prompt_version``.
    """
    user_msg = _USER_TMPL.format(
        expect=expect, transcript=_format_transcript(transcript)
    )
    return f"{_SYSTEM}\n\n{user_msg}"


# A transcript exercising every branch of the renderer: each role it formats,
# one it drops, a tool input, and a tool result far past any plausible cut.
_VERSION_PROBE = [
    TranscriptTurn(role="user", content="question"),
    TranscriptTurn(role="assistant", content="answer"),
    TranscriptTurn(
        role="tool_use", content="", tool_name="Write", tool_input={"path": "é"}
    ),
    TranscriptTurn(role="tool_result", content="", tool_output="x" * 100_000),
    TranscriptTurn(role="tool_result", content=""),
]


def _prompt_version() -> str:
    """Hash what the renderer *produces* for fixed inputs, not its source.

    A change to the templates or to the transcript formatting (a new truncation
    limit, say) moves the hash without anyone remembering to bump it.
    """
    rendered = render_judge_prompt("expectation", _VERSION_PROBE)
    rendered += render_judge_prompt("expectation", [])
    return hashlib.sha256(rendered.encode()).hexdigest()[:12]


# Names the renderer a run was judged with, so a saved run's judge input can be
# rebuilt with confidence: ``render_judge_prompt`` on a later release reproduces
# it only while this matches ``RunMeta.judge_prompt_version``.
JUDGE_PROMPT_VERSION = _prompt_version()


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


@dataclass(frozen=True)
class _AutoraterVerdict:
    """What one autorater call concluded about an attempt.

    ``errored`` is True when the autorater failed to yield a usable verdict at
    all (unparseable JSON, a malformed verdict, a failed or hung call). It is
    distinct from ``passed=False`` — a real judgment that the task failed.
    """

    passed: bool
    reasoning: str
    errored: bool = False
    # The code the autorater wrote in script mode, kept whether it passed,
    # failed or hung: the workdir it asserted on is gone once the attempt ends,
    # so the code is all that is left to debug the verdict with. ``None`` for a
    # direct verdict.
    script: str | None = None
    resolved_model: str | None = None
    seconds: float | None = None

    @classmethod
    def error(
        cls,
        reasoning: str,
        *,
        script: str | None = None,
        resolved_model: str | None = None,
        seconds: float | None = None,
    ) -> _AutoraterVerdict:
        """No usable verdict, and why."""
        return cls(
            passed=False,
            reasoning=reasoning,
            errored=True,
            script=script,
            resolved_model=resolved_model,
            seconds=seconds,
        )


def _parse_rich_response(raw: str, workdir: AttemptWorkdir) -> _AutoraterVerdict:
    """Parse an autorater response, running its script when it wrote one."""
    try:
        verdict = json.loads(raw)
    except json.JSONDecodeError:
        return _AutoraterVerdict.error(
            f"Judge returned unparseable response: {raw[:200]}"
        )

    # Valid JSON is not yet a verdict: anything off the two modes' shapes is no
    # verdict at all, never a pass or a fail (docs/adr/0001).
    if not isinstance(verdict, dict):
        return _AutoraterVerdict.error(f"Judge returned a non-object: {raw[:200]}")

    mode = verdict.get("mode", "verdict")
    reasoning = str(verdict.get("reasoning", ""))

    if mode == "script":
        code = verdict.get("code", "")
        if not isinstance(code, str):
            return _AutoraterVerdict.error(f"Judge returned non-string code: {code!r}")
        if not code:
            return _AutoraterVerdict.error("Judge returned empty script")
        passed, evidence = _run_inline_script(code, workdir, "check")
        detail = f"{reasoning} | script: {'ok' if passed else evidence}"
        if passed is None:
            return _AutoraterVerdict.error(detail, script=code)
        return _AutoraterVerdict(passed=passed, reasoning=detail, script=code)

    if mode != "verdict":
        return _AutoraterVerdict.error(f"Judge returned unknown mode: {mode!r}")
    passed = verdict.get("passed")
    # Not bool(): the string "false" is truthy.
    if not isinstance(passed, bool):
        return _AutoraterVerdict.error(
            f"Judge verdict has no boolean 'passed': {passed!r}"
        )
    return _AutoraterVerdict(passed=passed, reasoning=reasoning)


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

    prompt_version = JUDGE_PROMPT_VERSION

    def __init__(
        self,
        backend: str = DEFAULT_BACKEND,
        model: str | None = None,
        *,
        harness: PromptBackend | None = None,
    ) -> None:
        # The judge engine is a runtime axis, resolved from --judge-model (ADR
        # 0004). ``None`` means the judge CLI's own default model, and a run
        # that never calls an autorater (assert-only) records no judge model
        # rather than one that never ran.
        self.backend = backend
        self.model = model
        # The backend that answers the autorater's prompt, through the
        # ``run_prompt`` half of the backend seam. Built from ``backend`` on
        # first use, once per judge rather than per attempt; passed in by a
        # caller (a test) that answers the prompt itself. Worker threads share
        # the judge, so two may each build one on first use; that is harmless,
        # since a harness holds only its model.
        self._harness = harness

    def evaluate(
        self,
        task: TaskSpec,
        transcript: list[ConversationTurn],
        workdir: AttemptWorkdir,
    ) -> JudgeResult:
        assert_passed: bool | None = None
        assert_evidence: str | None = None
        static_result = _run_assert_from_task(task, workdir)
        if static_result is not None:
            assert_passed, assert_evidence = static_result

        autorater: _AutoraterVerdict | None = None
        autorater_passed: bool | None = None
        if task.expect:
            autorater = self._llm_evaluate(task, transcript, workdir)
            # An errored autorater yields no verdict: leave autorater_passed
            # None so it is dropped from the checks rather than counted as a
            # failure.
            autorater_passed = None if autorater.errored else autorater.passed

        # Rule B: only checks that produced a verdict count. A judge_error is
        # raised only when *no* verdict survives (see ADR-0001).
        checks = [c for c in (assert_passed, autorater_passed) if c is not None]

        result = JudgeResult(
            passed=all(checks) if checks else False,
            assert_passed=assert_passed,
            assert_evidence=assert_evidence,
            autorater_passed=autorater_passed,
            errored=not checks,
        )
        if autorater is not None:
            result.autorater_reasoning = autorater.reasoning
            result.resolved_model = autorater.resolved_model
            result.autorater_seconds = autorater.seconds
            result.autorater_script = autorater.script
        return result

    def _llm_evaluate(
        self,
        task: TaskSpec,
        transcript: list[ConversationTurn],
        workdir: AttemptWorkdir,
    ) -> _AutoraterVerdict:
        # An autorater is one bare prompt through a CLI agent — the same
        # backend adapters that run attempts also answer the judge, via the
        # ``run_prompt`` half of the backend seam.
        if self._harness is None:
            try:
                self._harness = get_harness(self.backend, self.model)
            except ValueError:
                return _AutoraterVerdict.error(
                    f"Unknown judge backend: {self.backend!r}"
                )
        harness = self._harness

        prompt = render_judge_prompt(task.expect or "", transcript)

        # In the workdir, not the spec dir: the judge grades what the agent left
        # there, and must not sit beside the answer key or write into the
        # author's repo (docs/adr/0026).
        # Timed around the model call alone: an assert script or building the
        # harness is not judge time (docs/CONTEXT.md → Judge time).
        started = time.monotonic()
        result = harness.run_prompt(prompt, cwd=workdir.path, timeout=60)
        # The harness times its own spawns, the waits between retries excluded.
        seconds = (
            result.seconds if result.seconds is not None else time.monotonic() - started
        )
        # A refusal that would fail every attempt's judge alike (a misconfigured
        # judge, an unavailable model, a spending cap) has already raised inside
        # the harness; what is left here is this one attempt's (docs/adr/0030).
        if result.error:
            return _AutoraterVerdict.error(
                result.error, resolved_model=result.resolved_model, seconds=seconds
            )
        verdict = _parse_rich_response(_strip_markdown_fence(result.text), workdir)
        return replace(verdict, resolved_model=result.resolved_model, seconds=seconds)
