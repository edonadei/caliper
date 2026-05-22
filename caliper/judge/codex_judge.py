from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from caliper.harness.base import ConversationTurn
from caliper.judge.autorater import _format_transcript, _SYSTEM, _USER_TMPL
from caliper.judge.base import Judge, JudgeResult
from caliper.schema.spec import JudgeConfig, TaskSpec

CODEX_APP_CLI = Path("/Applications/Codex.app/Contents/Resources/codex")


class CodexJudge(Judge):
    """Judge that uses `codex exec` instead of a provider SDK directly."""

    def __init__(self, config: JudgeConfig) -> None:
        self._config = config

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

        passed, reasoning = evaluate_with_codex(
            expect=task.expect,
            transcript=transcript,
            model=self._config.model,
            cwd=spec_dir,
        )

        return JudgeResult(
            passed=passed,
            reasoning=reasoning,
            autorater_passed=passed,
            autorater_reasoning=reasoning,
        )


def evaluate_with_codex(
    *,
    expect: str,
    transcript: list[ConversationTurn],
    model: str | None,
    cwd: str,
    timeout: int = 60,
) -> tuple[bool, str]:
    user_msg = _USER_TMPL.format(
        expect=expect,
        transcript=_format_transcript(transcript),
    )
    prompt = f"{_SYSTEM}\n\n{user_msg}"
    raw, error = _run_codex(prompt, model, cwd, timeout)
    if error:
        return False, error

    try:
        verdict = json.loads(_strip_markdown_fence(raw))
        passed = bool(verdict.get("passed", False))
        reasoning = str(verdict.get("reasoning", ""))
    except (json.JSONDecodeError, KeyError):
        passed = False
        reasoning = f"Judge returned unparseable response: {raw[:200]}"

    return passed, reasoning


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
