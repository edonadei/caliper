from __future__ import annotations

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


def evaluate_with_hermes(
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
    raw, error = _run_hermes(prompt, model, spec_dir, timeout)
    if error:
        return False, error, True, model
    passed, reasoning, errored = _parse_rich_response(
        _strip_markdown_fence(raw), spec_dir
    )
    # hermes' judge invocation (`-z`, final text only) doesn't surface the
    # resolved model, so we report the requested one (None on its own default).
    return passed, reasoning, errored, model


def _run_hermes(
    prompt: str, model: str | None, cwd: str, timeout: int
) -> tuple[str, str | None]:
    hermes = _hermes_command()
    if not hermes:
        return "", "hermes CLI not found"

    # A judge is a single autorater call: `hermes -z` prints only the final
    # response text, which is the JSON verdict we asked for. --ignore-rules keeps
    # persona/memory out of the grader; the judge reuses the real ~/.hermes.
    cmd = [hermes, "-z", prompt, "--ignore-rules"]
    if model:
        cmd[2:2] = ["--model", model]

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            encoding="utf-8",
            text=True,
            timeout=timeout,
            env=dict(os.environ),
            cwd=cwd,
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired:
        return "", "Judge timed out."
    except OSError as exc:
        return "", f"hermes judge failed: {exc}"

    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip()
        return "", f"hermes judge exited {proc.returncode}: {detail[:200]}"

    return proc.stdout.strip(), None


def _hermes_command() -> str | None:
    configured = os.environ.get("HERMES_CLI_PATH")
    if configured and Path(configured).exists():
        return configured
    return shutil.which("hermes")


def _strip_markdown_fence(raw: str) -> str:
    raw = raw.strip()
    if not raw.startswith("```"):
        return raw
    lines = raw.splitlines()
    return "\n".join(line for line in lines if not line.startswith("```")).strip()
