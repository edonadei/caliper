from __future__ import annotations

import json
import os
import re
import shutil
import sys
import uuid
from pathlib import Path
from typing import Callable

from caliper.harness.base import (
    ConversationTurn,
    CliHarness,
    ProcessResult,
    RunContext,
)
from caliper.schema.results import TokenUsage


def preferred_nvm_node_bin() -> str | None:
    """Return the bin/ path of the highest even-major nvm Node release, or None."""
    nvm_versions = Path.home() / ".nvm" / "versions" / "node"
    if not nvm_versions.exists():
        return None
    candidates: list[tuple[int, int, int, Path]] = []
    for node in nvm_versions.glob("v*/bin/node"):
        m = re.fullmatch(r"v(\d+)\.(\d+)\.(\d+)", node.parent.parent.name)
        if not m:
            continue
        major, minor, patch = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if major % 2 == 0:
            candidates.append((major, minor, patch, node.parent))
    if not candidates:
        return None
    return str(max(candidates, key=lambda item: item[:3])[3])


class ClaudeCodeHarness(CliHarness):
    def __init__(self, model: str | None = None) -> None:
        self._model = model

    @property
    def name(self) -> str:
        return "claude-code"

    def _prepare(self, ctx: RunContext) -> None:
        home = Path(ctx.isolated_home)
        (home / ".claude" / "commands").mkdir(parents=True, exist_ok=True)

        # Copy auth files from the real HOME so the CLI finds its credentials.
        # Without this, the isolated HOME causes claude to fall back to
        # ANTHROPIC_API_KEY (which may be absent or unfunded).
        real_home = Path.home()
        for src, dst in [
            (real_home / ".claude.json", home / ".claude.json"),
            (
                real_home / ".claude" / ".credentials.json",
                home / ".claude" / ".credentials.json",
            ),
        ]:
            if src.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)

        # On macOS, OAuth credentials may live in the Keychain rather than in
        # .credentials.json. Seed the isolated home so the subprocess can auth
        # without a browser login flow.
        creds_dst = home / ".claude" / ".credentials.json"
        if sys.platform == "darwin" and not creds_dst.exists():
            self._seed_credentials_from_keychain(creds_dst)

        ctx.extras["has_file_credentials"] = creds_dst.exists()

    def _command(
        self, ctx: RunContext
    ) -> tuple[list[str], str | None, Callable[[], None] | None]:
        cleanup: Callable[[], None] | None = None
        if ctx.skill_path:
            commands_dir = Path(ctx.isolated_home) / ".claude" / "commands"
            skill_src = Path(ctx.skill_path).expanduser()
            uid = uuid.uuid4().hex[:8]
            skill_file = commands_dir / f"{skill_src.stem}-vrd-{uid}.md"
            skill_file.write_text(skill_src.read_text())
            cleanup = lambda: skill_file.unlink(missing_ok=True)  # noqa: E731

        cmd = [
            "claude",
            "-p",
            ctx.prompt,
            "--output-format",
            "stream-json",
            "--verbose",
            "--dangerously-skip-permissions",
        ]
        if ctx.model:
            cmd += ["--model", ctx.model]
        return cmd, None, cleanup

    def _environment(self, ctx: RunContext) -> dict[str, str]:
        return self._build_env(
            ctx.isolated_home,
            ctx.extra_path,
            ctx.extras.get("has_file_credentials", False),
        )

    def _diagnose(self, proc: ProcessResult, final_output: str) -> str | None:
        text = "\n".join(part for part in (final_output, proc.stderr) if part).strip()
        if not text:
            return None

        returncode = proc.returncode
        lowered = text.lower()
        if returncode != 0 and self._looks_like_cli_startup_crash(text, lowered):
            summary = self._summarize_cli_crash(text)
            return (
                "Claude Code exited before the eval attempt could run because the "
                "Claude CLI crashed during startup.\n\n"
                "The Claude CLI returned:\n"
                f"  {summary}\n\n"
                "Fix the local Claude Code CLI or Node.js runtime, then rerun caliper. "
                "You can confirm the same failure outside caliper with "
                "`claude --version` or `claude -p 'Reply OK'`."
            )

        if "not logged in" in lowered or "please run /login" in lowered:
            return (
                "Claude Code is not logged in for the evaluation harness.\n\n"
                "caliper runs Claude Code in an isolated HOME so each attempt has no "
                "session history. The Claude CLI returned:\n"
                f"  {text}\n\n"
                "Run Claude Code login for this machine, then retry the eval. If "
                "`claude -p 'Reply OK'` works in your normal shell but caliper still "
                "fails, the harness is not finding or copying the credential store "
                "that your Claude Code install uses."
            )

        subscription_markers = (
            "does not have access to claude code",
            "disabled claude subscription access",
            "use an anthropic api key instead",
        )
        if any(marker in lowered for marker in subscription_markers):
            return (
                "Claude Code cannot run with the current account or organization "
                "configuration.\n\n"
                "The Claude CLI returned:\n"
                f"  {text}\n\n"
                "Your organization may have disabled Claude subscription access for "
                "Claude Code, or this account may not have Claude Code access. Use an "
                "Anthropic API key for eval runs, or ask your admin to enable Claude "
                "Code access for the account, then rerun caliper."
            )

        if returncode != 0 and "api key" in lowered and "anthropic" in lowered:
            return (
                "Claude Code exited before the eval attempt could run because it could "
                "not resolve Anthropic authentication.\n\n"
                "The Claude CLI returned:\n"
                f"  {text}\n\n"
                "Set `ANTHROPIC_API_KEY` or complete Claude Code login, then rerun "
                "caliper."
            )

        return None

    def _fallback(
        self,
        transcript: list[ConversationTurn],
        final_output: str,
        proc: ProcessResult,
    ) -> tuple[list[ConversationTurn], str]:
        # Claude's stream-json stdout is never salvageable as a raw turn; its own
        # last-assistant fallback in _parse_stream is the only fallback.
        return transcript, final_output

    def _error_field(self, proc: ProcessResult, final_output: str) -> str | None:
        if proc.timed_out:
            return "timeout"
        if proc.returncode != 0 and not final_output:
            return proc.stderr or None
        return None

    def _looks_like_cli_startup_crash(self, text: str, lowered: str) -> bool:
        return (
            "typeerror:" in lowered
            or "syntaxerror:" in lowered
            or "referenceerror:" in lowered
            or "file:///opt/homebrew/lib/node_modules/@anthropic-ai/claude-code/"
            in lowered
            or "node.js v" in lowered
        ) and "claude-code/cli.js" in lowered

    def _summarize_cli_crash(self, text: str) -> str:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        error_line = next(
            (
                line
                for line in lines
                if line.startswith(("TypeError:", "SyntaxError:", "ReferenceError:"))
            ),
            None,
        )
        node_line = next((line for line in lines if line.startswith("Node.js ")), None)
        stack_lines = [line for line in lines if "claude-code/cli.js:" in line]

        useful = []
        if stack_lines:
            useful.append(stack_lines[0])
        if error_line:
            useful.append(error_line)
        if node_line:
            useful.append(node_line)
        useful.extend(line for line in stack_lines[1:4] if line not in useful)

        if useful:
            return "\n  ".join(useful[:6])

        compact = re.sub(r"\s+", " ", text).strip()
        return compact[:500]

    def _seed_credentials_from_keychain(self, dst: Path) -> None:
        out = self._capture_output(
            [
                "security",
                "find-generic-password",
                "-s",
                "Claude Code-credentials",
                "-w",
            ],
            timeout=5,
        )
        if out:
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_text(out)

    def _build_env(
        self,
        isolated_home: str,
        extra_path: list[str],
        has_file_credentials: bool = False,
    ) -> dict[str, str]:
        base_path = os.environ.get("PATH", "")
        path_prefixes = []

        nvm_node_bin = preferred_nvm_node_bin()
        if nvm_node_bin:
            path_prefixes.append(nvm_node_bin)

        # On macOS, IDE-launched processes often have a stripped PATH that
        # omits Homebrew prefixes. Prepend them when present so tools installed
        # via Homebrew (e.g. on Apple Silicon at /opt/homebrew/bin) are found.
        if sys.platform == "darwin":
            homebrew_candidates = [
                "/opt/homebrew/bin",
                "/opt/homebrew/sbin",
                "/usr/local/bin",
            ]
            path_prefixes.extend(p for p in homebrew_candidates if os.path.isdir(p))

        if extra_path:
            path_prefixes = extra_path + path_prefixes

        base_parts = [p for p in base_path.split(os.pathsep) if p]
        path_prefixes = list(dict.fromkeys(path_prefixes))
        prefix_set = set(path_prefixes)
        base_parts = [p for p in base_parts if p not in prefix_set]
        additions = path_prefixes
        if additions:
            base_path = os.pathsep.join(additions + base_parts)

        env: dict[str, str] = {
            "HOME": isolated_home,
            "PATH": base_path,
        }

        # macOS uses TMPDIR for the per-user secure temp directory; Node.js
        # (and therefore the claude CLI) reads it to locate scratch space.
        if sys.platform == "darwin" and "TMPDIR" in os.environ:
            env["TMPDIR"] = os.environ["TMPDIR"]

        # Only forward API keys when there are no file-based credentials and
        # no Keychain credentials — avoids overriding valid OAuth auth with a
        # potentially unfunded key.
        if not has_file_credentials:
            for key in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
                if key in os.environ:
                    env[key] = os.environ[key]

        return env

    def _usage(self, proc: ProcessResult, ctx: RunContext) -> TokenUsage | None:
        """Read the ``result`` event's ``usage``. Claude's ``input_tokens`` is
        already non-cached, so the mapping is direct."""
        for line in proc.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("type") != "result":
                continue
            usage = event.get("usage")
            if not isinstance(usage, dict):
                return None
            return TokenUsage(
                input_tokens=usage.get("input_tokens"),
                output_tokens=usage.get("output_tokens"),
                cache_read_tokens=usage.get("cache_read_input_tokens"),
                cache_creation_tokens=usage.get("cache_creation_input_tokens"),
            )
        return None

    def _parse_stream(self, stdout: str) -> tuple[list[ConversationTurn], str]:
        transcript: list[ConversationTurn] = []
        final_output = ""

        for line in stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue

            etype = event.get("type", "")

            if etype == "assistant":
                for block in event.get("message", {}).get("content", []):
                    btype = block.get("type", "")
                    if btype == "text":
                        transcript.append(
                            ConversationTurn(role="assistant", content=block["text"])
                        )
                    elif btype == "tool_use":
                        transcript.append(
                            ConversationTurn(
                                role="tool_use",
                                content=f"[tool: {block.get('name')}]",
                                tool_name=block.get("name"),
                                tool_input=block.get("input"),
                            )
                        )

            elif etype == "tool_result":
                content = event.get("content", "")
                if isinstance(content, list):
                    content = " ".join(
                        c.get("text", "") for c in content if isinstance(c, dict)
                    )
                transcript.append(
                    ConversationTurn(
                        role="tool_result", content=content, tool_output=content
                    )
                )

            elif etype == "result":
                final_output = event.get("result", "")

        if not final_output and transcript:
            for turn in reversed(transcript):
                if turn.role == "assistant" and turn.content:
                    final_output = turn.content
                    break

        return transcript, final_output
