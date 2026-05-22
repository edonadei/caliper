from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from caliper.harness.base import (
    AttemptResult,
    ConversationTurn,
    HarnessBackend,
    HarnessConfigurationError,
)

CODEX_APP_CLI = Path("/Applications/Codex.app/Contents/Resources/codex")


class CodexHarness(HarnessBackend):
    def __init__(self, model: str | None = None) -> None:
        self._model = model

    @property
    def name(self) -> str:
        return "codex"

    def run(
        self,
        task_id: str,
        attempt: int,
        prompt: str,
        *,
        skill_path: str | None,
        model: str | None,
        timeout: int,
        isolated_home: str,
        extra_path: list[str] | None = None,
    ) -> AttemptResult:
        full_prompt = self._inject_skill(prompt, skill_path)
        effective_model = model or self._model
        start = time.monotonic()

        if not self._cli_available():
            raise HarnessConfigurationError(
                "Codex CLI is not available for the `codex` backend.\n\n"
                "Caliper does not fall back to the OpenAI API for `backend: codex`. "
                "Install and authenticate the Codex CLI, or explicitly use "
                "`backend: openai-api` for API-based evals."
            )

        self._copy_codex_config(isolated_home)
        output, exit_code, error = self._run_cli(
            full_prompt,
            effective_model,
            timeout,
            isolated_home,
            extra_path or [],
        )
        diagnostic = self._diagnose_configuration_error(exit_code, output, error)
        if diagnostic:
            raise HarnessConfigurationError(diagnostic)

        duration = time.monotonic() - start
        transcript = [ConversationTurn(role="assistant", content=output)] if output else []

        return AttemptResult(
            task_id=task_id,
            attempt=attempt,
            transcript=transcript,
            final_output=output,
            exit_code=exit_code,
            duration_seconds=duration,
            error=error,
        )

    def _inject_skill(self, prompt: str, skill_path: str | None) -> str:
        if not skill_path:
            return prompt
        import re

        skill_src = Path(skill_path).expanduser()
        if not skill_src.exists():
            return prompt

        raw = skill_src.read_text()
        # Strip YAML frontmatter
        body = re.sub(r"^---\n.*?\n---\n", "", raw, flags=re.DOTALL).strip()
        return f"[Skill context]\n{body}\n[End skill context]\n\n{prompt}"

    def _cli_available(self) -> bool:
        codex = self._codex_command()
        if codex is None:
            return False
        try:
            result = subprocess.run(
                [codex, "--version"],
                capture_output=True,
                text=True,
                timeout=5,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        return result.returncode == 0

    def _run_cli(
        self,
        prompt: str,
        model: str | None,
        timeout: int,
        isolated_home: str,
        extra_path: list[str],
    ) -> tuple[str, int, str | None]:
        codex = self._codex_command() or "codex"
        env = self._build_env(isolated_home, extra_path)
        try:
            cmd = [
                codex,
                "exec",
                "--skip-git-repo-check",
                "--dangerously-bypass-approvals-and-sandbox",
                "--color",
                "never",
                "-",
            ]
            if model:
                cmd[2:2] = ["--model", model]

            result = subprocess.run(
                cmd,
                input=prompt,
                capture_output=True,
                encoding="utf-8",
                text=True,
                timeout=timeout,
                env=env,
                cwd=isolated_home,
            )
            return result.stdout.strip(), result.returncode, result.stderr.strip() or None
        except subprocess.TimeoutExpired:
            return "", 124, "timeout"
        except OSError as exc:
            return "", 1, f"codex CLI failed: {exc}"

    def _build_env(self, isolated_home: str, extra_path: list[str]) -> dict[str, str]:
        path = os.environ.get("PATH", "")
        if extra_path:
            path = os.pathsep.join(extra_path) + os.pathsep + path

        env = {
            "HOME": isolated_home,
            "PATH": path,
        }
        for key in ("LANG", "LC_ALL", "TERM", "TMPDIR"):
            if key in os.environ:
                env[key] = os.environ[key]
        return env

    def _codex_command(self) -> str | None:
        configured = os.environ.get("CODEX_CLI_PATH")
        if configured and Path(configured).exists():
            return configured
        if CODEX_APP_CLI.exists():
            return str(CODEX_APP_CLI)
        return shutil.which("codex")

    def _copy_codex_config(self, isolated_home: str) -> None:
        home = Path(isolated_home)
        codex_home = home / ".codex"
        real_codex_home = Path.home() / ".codex"
        for filename in ("auth.json", "config.toml"):
            src = real_codex_home / filename
            if src.exists():
                codex_home.mkdir(parents=True, exist_ok=True)
                dst = codex_home / filename
                if filename == "config.toml":
                    dst.write_text(self._strip_top_level_model_config(src.read_text()))
                else:
                    shutil.copy2(src, dst)

    def _strip_top_level_model_config(self, config: str) -> str:
        filtered: list[str] = []
        in_table = False
        for line in config.splitlines():
            stripped = line.strip()
            if stripped.startswith("["):
                in_table = True
            if not in_table and stripped.startswith("model ="):
                continue
            filtered.append(line)
        return "\n".join(filtered) + ("\n" if config.endswith("\n") else "")

    def _diagnose_configuration_error(
        self,
        returncode: int,
        stdout: str,
        stderr: str | None,
    ) -> str | None:
        if returncode == 0:
            return None

        text = "\n".join(part for part in (stdout, stderr or "") if part).strip()
        lowered = text.lower()

        model_markers = (
            "requires a newer version of codex",
            "please upgrade to the latest app or cli",
            "model is not supported",
            "model is not available",
        )
        if any(marker in lowered for marker in model_markers):
            summary = self._summarize_cli_configuration_error(text)
            return (
                "Codex CLI cannot run the requested model with this account or "
                "installed version.\n\n"
                "Caliper uses `codex exec` for `backend: codex`. The Codex CLI "
                "returned:\n"
                f"  {summary}\n\n"
                "Omit `model` to use the Codex CLI default, upgrade the Codex app "
                "or CLI, or choose a model supported by the installed Codex CLI "
                "and account, then retry the eval."
            )

        auth_markers = (
            "401 unauthorized",
            "not logged in",
            "please login",
            "please run /login",
            "authentication",
            "invalid api key",
            "api key",
            "subscription",
            "chatgpt account",
        )
        if any(marker in lowered for marker in auth_markers):
            return (
                "Codex CLI cannot run with the current subscription/authentication "
                "configuration.\n\n"
                "Caliper uses `codex exec` for `backend: codex` and does not fall "
                "back to the OpenAI API. The Codex CLI returned:\n"
                f"  {text}\n\n"
                "Run `codex login` and verify `codex exec` works in your normal "
                "shell, then retry the eval. If you intended to use API billing, "
                "set `backend: openai-api` explicitly."
            )

        if sys.platform == "darwin" and "operation not permitted" in lowered:
            return (
                "Codex CLI was blocked by the operating system while running under "
                "Caliper.\n\n"
                f"The Codex CLI returned:\n  {text}"
            )

        return None

    def _summarize_cli_configuration_error(self, text: str) -> str:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        version_line = next((line for line in lines if line.startswith("OpenAI Codex")), None)
        error_lines = [line for line in lines if line.startswith("ERROR:")]
        detail_lines = [
            line
            for line in lines
            if (
                "requires a newer version of Codex" in line
                or "model is not supported" in line
                or "model is not available" in line
            )
            and not line.startswith(("stream error:", "ERROR:"))
        ]

        useful = []
        if version_line:
            useful.append(version_line)
        useful.extend(error_lines[:2])
        useful.extend(line for line in detail_lines[:2] if line not in useful)
        if useful:
            return "\n  ".join(useful[:5])
        return text[:500]
