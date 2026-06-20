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
) -> tuple[bool, str]:
    user_msg = _USER_TMPL.format(
        expect=expect,
        transcript=_format_transcript(transcript),
    )
    prompt = f"{_SYSTEM}\n\n{user_msg}"
    raw, error = _run_claude_code(prompt, model)
    if error:
        return False, error
    return _parse_rich_response(raw, spec_dir)


def _judge_env() -> dict[str, str]:
    env = dict(os.environ)
    nvm_bin = preferred_nvm_node_bin()
    if nvm_bin:
        env["PATH"] = nvm_bin + os.pathsep + env.get("PATH", "")
    return env


def _run_claude_code(prompt: str, model: str | None) -> tuple[str, str | None]:
    cmd = ["claude", "-p", prompt, "--output-format", "text"]
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
        raw = proc.stdout.strip()
    except subprocess.TimeoutExpired:
        return "", "Judge timed out."

    if raw.startswith("```"):
        lines = raw.splitlines()
        raw = "\n".join(line for line in lines if not line.startswith("```")).strip()

    return raw, None
