from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from caliper.harness.base import ConversationTurn
from caliper.judge.script_assert import (
    _SYSTEM,
    _USER_TMPL,
    _format_transcript,
    _parse_rich_response,
)


def evaluate_with_pi(
    *,
    expect: str,
    transcript: list[ConversationTurn],
    model: str | None,
    spec_dir: str,
    timeout: int = 60,
) -> tuple[bool, str, bool, str | None]:
    user_msg = _USER_TMPL.format(
        expect=expect,
        transcript=_format_transcript(transcript),
    )
    prompt = f"{_SYSTEM}\n\n{user_msg}"
    raw, error = _run_pi(prompt, model, spec_dir, timeout)
    if error:
        return False, error, True, model
    passed, reasoning, errored = _parse_rich_response(raw, spec_dir)
    # pi's judge invocation doesn't surface the resolved model, so we can only
    # report the one that was requested (None when its own default was used).
    return passed, reasoning, errored, model


def _run_pi(
    prompt: str, model: str | None, cwd: str, timeout: int
) -> tuple[str, str | None]:
    pi = _pi_command()
    if not pi:
        return "", "pi CLI not found"

    cmd = [pi, "--print", "--mode", "json", "--no-session", "--approve"]
    if model:
        cmd += ["--model", model]
    cmd.append(prompt)

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=dict(os.environ),
            cwd=cwd,
            # --print reads stdin for trust confirmations otherwise and hangs.
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired:
        return "", "Judge timed out."
    except OSError as exc:
        return "", f"pi judge failed: {exc}"

    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip()
        return "", f"pi judge exited {proc.returncode}: {detail[:200]}"

    return _final_assistant_message(proc.stdout), None


def _final_assistant_message(stdout: str) -> str:
    """Extract the last assistant message from pi's JSON event stream."""
    final = ""
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict) or event.get("type") != "message_end":
            continue
        message = event.get("message")
        if isinstance(message, dict) and message.get("role") == "assistant":
            text = _flatten_text(message.get("content"))
            if text:
                final = text
    return _strip_markdown_fence(final)


def _flatten_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        return "".join(parts).strip()
    return ""


def _strip_markdown_fence(raw: str) -> str:
    raw = raw.strip()
    if not raw.startswith("```"):
        return raw
    lines = raw.splitlines()
    return "\n".join(line for line in lines if not line.startswith("```")).strip()


def _pi_command() -> str | None:
    configured = os.environ.get("PI_CLI_PATH")
    if configured and Path(configured).exists():
        return configured
    return shutil.which("pi")
