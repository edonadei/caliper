from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Callable

from caliper.harness.base import (
    ConversationTurn,
    CliHarness,
    ProcessResult,
    PromptCall,
    PromptResult,
    RunContext,
)
from caliper.harness.prompt_failure import (
    PromptFailure,
    PromptFailureKind,
    classify_claude_api_error_status,
)
from caliper.harness.mcp import resolve_servers
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

    supports_mcp = True
    # The CLI classifies a real skill only at .claude/skills/<name>/SKILL.md and
    # exposes the agent's choice as a dedicated Skill tool call naming it.
    activation_tool_names = frozenset({"Skill"})
    # `claude setup-token` is the documented way to authenticate a headless run,
    # and the stripped HOME is exactly the case it exists for.
    env_passthrough = CliHarness.env_passthrough + ("CLAUDE_CODE_OAUTH_TOKEN",)

    @property
    def name(self) -> str:
        return "claude-code"

    def skills_root(self, ctx: RunContext) -> Path:
        return Path(ctx.isolated_home) / ".claude" / "skills"

    @staticmethod
    def _credentials_file(ctx: RunContext) -> Path:
        """Where the CLI looks for file-based credentials in the isolated home."""
        return Path(ctx.isolated_home) / ".claude" / ".credentials.json"

    def seed_files(self, ctx: RunContext) -> list[tuple[Path, Path]]:
        # Auth files from the real HOME, so the CLI finds its credentials.
        # Without them the isolated HOME makes claude fall back to
        # ANTHROPIC_API_KEY (which may be absent or unfunded).
        real_home = Path.home()
        home = Path(ctx.isolated_home)
        return [
            (real_home / ".claude.json", home / ".claude.json"),
            (real_home / ".claude" / ".credentials.json", self._credentials_file(ctx)),
        ]

    def _prepare(self, ctx: RunContext) -> None:
        (Path(ctx.isolated_home) / ".claude").mkdir(parents=True, exist_ok=True)

        # On macOS, OAuth credentials may live in the Keychain rather than in
        # .credentials.json. Seed the isolated home so the subprocess can auth
        # without a browser login flow.
        creds_dst = self._credentials_file(ctx)
        if sys.platform == "darwin" and not creds_dst.exists():
            self._seed_credentials_from_keychain(creds_dst)

        if ctx.user_customizations and ctx.spec_mcp_names:
            self._drop_shadowed_user_servers(ctx)

    def _drop_shadowed_user_servers(self, ctx: RunContext) -> None:
        """Remove the user's servers that share a name with a declared one.

        When inheriting (the default) the attempt keeps the user-scope ``mcpServers``
        from the seeded ``.claude.json``, and the spec wins a name clash
        (docs/adr/0028). Rather than rely on how the CLI orders its scopes, the
        clashing entries are taken out of the isolated copy, so the only server
        by that name is the one ``--mcp-config`` supplies — or none, when
        ``--ablate`` removed it. The user's real file is never touched.
        """
        path = Path(ctx.isolated_home) / ".claude.json"
        if not path.exists():
            return
        try:
            config = json.loads(path.read_text())
        except (json.JSONDecodeError, UnicodeDecodeError):
            return
        user_servers = config.get("mcpServers") if isinstance(config, dict) else None
        if not isinstance(user_servers, dict):
            return
        shadowed = [name for name in ctx.spec_mcp_names if name in user_servers]
        if not shadowed:
            return
        for name in shadowed:
            del user_servers[name]
        path.write_text(json.dumps(config, indent=2))

    def _command(
        self, ctx: RunContext
    ) -> tuple[list[str], str | None, Callable[[], None] | None]:
        # The skill neighbourhood is installed at .claude/skills/<name>/ by the
        # template method and left for the agent to discover (docs/adr/0013);
        # only the MCP config is staged per attempt, and removed after the
        # process exits.
        cmd = [
            "claude",
            "-p",
            ctx.prompt,
            "--output-format",
            "stream-json",
            "--verbose",
            "--dangerously-skip-permissions",
        ]

        mcp_config = self._materialize_mcp_config(ctx)
        # --strict-mcp-config so the attempt sees ONLY the declared servers —
        # never the account's claude.ai connectors, which the seeded login
        # brings along otherwise (docs/adr/0026). The config file (which may hold
        # resolved secrets) lives in the 0700 run tempdir, never argv.
        cmd += ["--mcp-config", str(mcp_config)]
        # Inheriting (the default) drops it: the declared servers then merge with the
        # seeded user config and the account's connectors (docs/adr/0028).
        if not ctx.user_customizations:
            cmd.append("--strict-mcp-config")

        if ctx.model:
            cmd += ["--model", ctx.model]

        return cmd, None, lambda: mcp_config.unlink(missing_ok=True)

    def _materialize_mcp_config(self, ctx: RunContext) -> Path:
        """Write the declared MCP servers into ``.caliper-mcp.json`` for the run.

        Both transports are emitted in Claude Code's ``mcpServers`` shape: the
        common rendering from ``resolve_servers`` (which already interpolated
        every ``${VAR}`` at the harness boundary), plus Claude Code's one
        spelling difference — a remote server names its transport explicitly via
        ``type``. The file may hold resolved secrets, so it lives in the 0700
        run tempdir and is kept ``0600``.

        No ``mcp:`` block, an authored ``mcp: {}``, and a block whose servers
        were all ablated all write an empty ``mcpServers``: the attempt sees zero
        servers rather than whatever the seeded user config and account carry —
        unless the run inherits those too (the default; see ``_command``).
        """
        servers: dict[str, dict] = {}
        for name, resolved in resolve_servers(ctx.mcp_servers or {}).items():
            entry = resolved.entry()
            if resolved.is_remote:
                entry = {"type": resolved.type, **entry}
            servers[name] = entry

        config_path = Path(ctx.isolated_home) / ".caliper-mcp.json"
        config_path.write_text(json.dumps({"mcpServers": servers}))
        config_path.chmod(0o600)
        return config_path

    def _environment(self, ctx: RunContext) -> dict[str, str]:
        env = self._isolated_env(ctx, path_prefixes=self._path_prefixes())

        # Only forward API keys when there are no file-based credentials and no
        # Keychain credentials — avoids overriding valid OAuth auth with a
        # potentially unfunded key. Read off the home rather than remembered
        # from ``_prepare``: the file is the fact, and it is still there.
        if not self._credentials_file(ctx).exists():
            for key in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
                if key in os.environ:
                    env[key] = os.environ[key]
        return env

    def _path_prefixes(self) -> list[str]:
        """Interpreter and package-manager directories the CLI needs on ``PATH``.

        The claude CLI is a Node program, so an nvm-managed Node has to be found
        before the system one; on macOS an IDE-launched process often has a
        stripped PATH that omits the Homebrew prefixes the agent's own tools live
        in. Everything else about the environment is the shared isolated one.
        """
        prefixes: list[str] = []

        nvm_node_bin = preferred_nvm_node_bin()
        if nvm_node_bin:
            prefixes.append(nvm_node_bin)

        if sys.platform == "darwin":
            prefixes.extend(
                p
                for p in ("/opt/homebrew/bin", "/opt/homebrew/sbin", "/usr/local/bin")
                if os.path.isdir(p)
            )
        return prefixes

    def _diagnose(self, proc: ProcessResult, final_output: str) -> str | None:
        # Read off the CLI's own result envelope, not the text: an agent can
        # write about a 404 without being one. Same classification the judge's
        # prompt path uses (issue #75, docs/adr/0001).
        unavailable = _unavailable_model_message(proc.stdout)
        if unavailable is not None:
            model_part = f" '{self._model}'" if self._model else ""
            return (
                f"Claude Code cannot run the requested model{model_part}.\n\n"
                "The Claude CLI returned:\n"
                f"  {unavailable}\n\n"
                "Pass `--model claude-code:<model>` with a model this account "
                "can use, or `--model claude-code` for the CLI default, then "
                "retry the eval."
            )

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
        # Claude's stream-json stdout is never salvageable as a raw turn; the
        # template's last-assistant tail is the only fallback.
        return transcript, final_output

    def _error_field(self, proc: ProcessResult, final_output: str) -> str | None:
        if proc.timed_out:
            return "timeout"
        if proc.returncode != 0 and not final_output:
            return proc.stderr or None
        return None

    # --- bare prompt call (the judge's half of the seam) -------------------

    def _prompt_command(self, prompt: str, model: str | None) -> PromptCall:
        # JSON output (over plain text) so we can read the *concrete* model
        # Claude used — the answer lives in `.result`, the model in `.modelUsage`.
        # --strict-mcp-config with no --mcp-config: the judge sees no MCP
        # servers, so the account's claude.ai connectors neither reach it nor
        # get mistaken for the attempt's tools (docs/adr/0026).
        cmd = ["claude", "-p", prompt, "--output-format", "json", "--strict-mcp-config"]
        if model:
            cmd += ["--model", model]
        return PromptCall(cmd)

    def _prompt_environment(self) -> dict[str, str]:
        env = dict(os.environ)
        nvm_bin = preferred_nvm_node_bin()
        if nvm_bin:
            env["PATH"] = nvm_bin + os.pathsep + env.get("PATH", "")
        return env

    def _prompt_output(self, proc: ProcessResult, model: str | None) -> PromptResult:
        classified = _classify_claude_prompt_failure(proc.stdout, model)
        if classified is not None:
            return classified

        # Unclassified errors still flow text through so the caller's verdict
        # parse can report an unusable response (see PR #61).
        text, resolved = _extract_verdict_and_model(proc.stdout, model)
        return PromptResult(text=text, resolved_model=resolved, error=None)

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

    def _loaded_user_customizations(
        self, proc: ProcessResult, ctx: RunContext
    ) -> list[str] | None:
        """The non-declared servers the CLI's ``init`` event says it loaded.

        The stream opens with a ``system``/``init`` event whose ``mcp_servers``
        lists every server the CLI configured — user config, account connectors
        and ``--mcp-config`` alike — each with a connection status. Every name
        counts, whatever its status: the record is what the attempt was given,
        not what happened to connect. ``None`` when no ``init`` event arrived.
        """
        declared = ctx.spec_mcp_names
        for line in proc.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
                continue
            if event.get("type") != "system" or event.get("subtype") != "init":
                continue
            servers = event.get("mcp_servers")
            if not isinstance(servers, list):
                return None
            names = {
                server.get("name")
                for server in servers
                if isinstance(server, dict) and isinstance(server.get("name"), str)
            }
            return sorted(names - declared)
        return None

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

            elif etype == "user":
                # Claude Code streams each tool's output back as a user turn
                # carrying ``tool_result`` blocks; plain user text is the prompt.
                content = event.get("message", {}).get("content", [])
                for block in content if isinstance(content, list) else []:
                    if isinstance(block, dict) and block.get("type") == "tool_result":
                        text = _tool_result_text(block.get("content", ""))
                        transcript.append(
                            ConversationTurn(
                                role="tool_result", content=text, tool_output=text
                            )
                        )

            elif etype == "tool_result":
                # Older stream shape: a top-level tool_result event.
                text = _tool_result_text(event.get("content", ""))
                transcript.append(
                    ConversationTurn(role="tool_result", content=text, tool_output=text)
                )

            elif etype == "result":
                final_output = event.get("result", "")

        return transcript, final_output


def _tool_result_text(content: object) -> str:
    """A tool result's text, whether it came as a string or a list of blocks."""
    if isinstance(content, list):
        return " ".join(c.get("text", "") for c in content if isinstance(c, dict))
    return content if isinstance(content, str) else ""


def _envelope_failure(envelope: object) -> PromptFailure | None:
    """The classified provider failure a CLI ``result`` envelope reports, if any."""
    if not isinstance(envelope, dict) or not envelope.get("is_error"):
        return None

    status = envelope.get("api_error_status")
    if not isinstance(status, int):
        return None

    kind = classify_claude_api_error_status(status)
    if kind is None:
        return None

    message = str(envelope.get("result", "")).strip() or f"API error {status}"
    return PromptFailure(kind=kind, message=message, status=status)


def _unavailable_model_message(stdout: str) -> str | None:
    """The CLI's message when its closing ``result`` event is a model 404."""
    for line in reversed(stdout.splitlines()):
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict) or event.get("type") != "result":
            continue
        failure = _envelope_failure(event)
        if failure is not None and failure.kind is PromptFailureKind.MODEL_UNAVAILABLE:
            return failure.message
        return None
    return None


def _classify_claude_prompt_failure(
    stdout: str, model: str | None
) -> PromptResult | None:
    """Return a classified upstream failure, or None to keep today's text path."""
    try:
        envelope = json.loads(stdout.strip())
    except json.JSONDecodeError:
        return None
    failure = _envelope_failure(envelope)
    if failure is None:
        return None
    # Carry the structural failure; the judge switches on ``failure.kind`` to
    # build the user-facing message (see caliper/judge/script_assert.py).
    return PromptResult(
        text="",
        resolved_model=model,
        error=failure.message,
        failure=failure,
    )


def _extract_verdict_and_model(
    stdout: str, requested_model: str | None
) -> tuple[str, str | None]:
    """Pull the answer text and concrete model from Claude's JSON envelope.

    Falls back to treating stdout as the raw answer (and the requested model)
    if the envelope is missing or unparseable, so a CLI change can't break the
    caller outright.
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
