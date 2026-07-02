from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from caliper.harness.base import ConversationTurn
from caliper.judge.script_assert import (
    _SYSTEM,
    _USER_TMPL,
    _format_transcript,
    _parse_rich_response,
)

CODEX_APP_CLI = Path("/Applications/Codex.app/Contents/Resources/codex")


def evaluate_with_codex(
    *,
    expect: str,
    transcript: list[ConversationTurn],
    model: str | None,
    cwd: str,
    timeout: int = 60,
) -> tuple[bool, str, bool]:
    user_msg = _USER_TMPL.format(
        expect=expect,
        transcript=_format_transcript(transcript),
    )
    prompt = f"{_SYSTEM}\n\n{user_msg}"
    raw, error = _run_codex(prompt, model, cwd, timeout)
    if error:
        return False, error, True

    return _parse_rich_response(_strip_markdown_fence(raw), cwd)


def _run_codex(
    prompt: str,
    model: str | None,
    cwd: str,
    timeout: int,
) -> tuple[str, str | None]:
    codex = _codex_command()
    if not codex:
        return "", "codex CLI not found"

    output_path = ""
    try:
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as output_file:
            output_path = output_file.name

        cmd = [
            codex,
            "exec",
            "--skip-git-repo-check",
            "--dangerously-bypass-approvals-and-sandbox",
            "--color",
            "never",
            "--output-last-message",
            output_path,
            "-",
        ]
        if model:
            cmd[2:2] = ["--model", model]

        proc = subprocess.run(
            cmd,
            input=prompt,
            capture_output=True,
            encoding="utf-8",
            text=True,
            timeout=timeout,
            env=dict(os.environ),
            cwd=cwd,
        )
        output_file_path = Path(output_path)
        raw = output_file_path.read_text().strip() if output_file_path.exists() else ""
    except subprocess.TimeoutExpired:
        return "", "Judge timed out."
    except OSError as exc:
        return "", f"codex judge failed: {exc}"
    finally:
        if output_path:
            Path(output_path).unlink(missing_ok=True)

    raw = raw or proc.stdout.strip()
    if proc.returncode != 0:
        detail = _extract_codex_error(proc.stderr) or _extract_codex_error(raw)
        return raw, detail or f"codex judge exited {proc.returncode}"
    return raw, None


def _codex_command() -> str | None:
    configured = os.environ.get("CODEX_CLI_PATH")
    if configured and Path(configured).exists():
        return configured
    if CODEX_APP_CLI.exists():
        return str(CODEX_APP_CLI)
    return shutil.which("codex")


def _strip_markdown_fence(raw: str) -> str:
    raw = raw.strip()
    if not raw.startswith("```"):
        return raw
    lines = raw.splitlines()
    return "\n".join(line for line in lines if not line.startswith("```")).strip()


def _extract_codex_error(output: str) -> str | None:
    for line in reversed(output.splitlines()):
        line = line.strip()
        if not line:
            continue
        if line.startswith("ERROR:"):
            candidate = line.removeprefix("ERROR:").strip()
            message = _error_message_from_json(candidate)
            return f"codex judge failed: {message or candidate}"
        message = _error_message_from_json(line)
        if message:
            return f"codex judge failed: {message}"
    return None


def _error_message_from_json(candidate: str) -> str | None:
    try:
        payload = json.loads(candidate)
    except json.JSONDecodeError:
        return None

    error = payload.get("error")
    if isinstance(error, dict):
        message = error.get("message")
        if isinstance(message, str) and message:
            return message
    return None
