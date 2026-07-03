from __future__ import annotations

import json
import os
import subprocess

from caliper.harness.base import ConversationTurn
from caliper.harness.claude_code import preferred_nvm_node_bin
from caliper.judge.script_assert import (
    _SYSTEM,
    _USER_TMPL,
    _format_transcript,
    _parse_rich_response,
)


def evaluate_with_claude_code(
    *,
    expect: str,
    transcript: list[ConversationTurn],
    model: str | None,
    spec_dir: str,
) -> tuple[bool, str, bool, str | None]:
    user_msg = _USER_TMPL.format(
        expect=expect,
        transcript=_format_transcript(transcript),
    )
    prompt = f"{_SYSTEM}\n\n{user_msg}"
    raw, resolved_model, error = _run_claude_code(prompt, model)
    if error:
        return False, error, True, resolved_model
    passed, reasoning, errored = _parse_rich_response(raw, spec_dir)
    return passed, reasoning, errored, resolved_model


def _judge_env() -> dict[str, str]:
    env = dict(os.environ)
    nvm_bin = preferred_nvm_node_bin()
    if nvm_bin:
        env["PATH"] = nvm_bin + os.pathsep + env.get("PATH", "")
    return env


def _run_claude_code(
    prompt: str, model: str | None
) -> tuple[str, str | None, str | None]:
    # JSON output (over plain text) so we can read the *concrete* model Claude
    # used — its verdict text lives in `.result` and the model in `.modelUsage`.
    cmd = ["claude", "-p", prompt, "--output-format", "json"]
    if model:
        cmd += ["--model", model]

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60,
            env=_judge_env(),
        )
    except subprocess.TimeoutExpired:
        return "", model, "Judge timed out."

    raw, resolved_model = _extract_verdict_and_model(proc.stdout, model)

    if raw.startswith("```"):
        lines = raw.splitlines()
        raw = "\n".join(line for line in lines if not line.startswith("```")).strip()

    return raw, resolved_model, None


def _extract_verdict_and_model(
    stdout: str, requested_model: str | None
) -> tuple[str, str | None]:
    """Pull the verdict text and concrete model from Claude's JSON envelope.

    Falls back to treating stdout as the raw verdict (and the requested model)
    if the envelope is missing or unparseable, so a CLI change can't break the
    judge outright.
    """
    stripped = stdout.strip()
    try:
        envelope = json.loads(stripped)
    except json.JSONDecodeError:
        return stripped, requested_model
    if not isinstance(envelope, dict):
        return stripped, requested_model

    verdict = str(envelope.get("result", "")).strip() or stripped
    model_usage = envelope.get("modelUsage")
    resolved = None
    if isinstance(model_usage, dict) and model_usage:
        resolved = next(iter(model_usage))
    return verdict, resolved or requested_model
