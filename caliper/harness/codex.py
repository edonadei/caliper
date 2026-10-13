from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from functools import partial
from pathlib import Path

from caliper.backends import mcp_tool_name
from caliper.harness.base import (
    AgentReport,
    CliHarness,
    ConversationTurn,
    HarnessConfigurationError,
    ProcessResult,
    PromptCall,
    RunContext,
    stream_events,
)
from caliper.harness.cli_config import TOML, CliConfig
from caliper.harness.mcp import ResolvedMcpServer
from caliper.harness.refusal import AUTH_MARKERS, ConfigSignal
from caliper.schema.results import TokenUsage
from caliper.schema.spec import PlantedTurn

CODEX_APP_CLI = Path("/Applications/Codex.app/Contents/Resources/codex")

# The ChatGPT login in auth.json carries the account's hosted connectors
# (mcp__codex_apps__*) and remote plugins, which stripping [mcp_servers] can't
# reach. A -c override beats any [features] table in the seeded config.toml,
# so neither an isolated attempt nor the judge sees them (docs/adr/0026); an
# attempt loading user customizations leaves them on (docs/adr/0028).
NO_ACCOUNT_CONNECTORS = ("-c", "features.apps=false", "-c", "features.plugins=false")

# The server name codex's hosted connectors surface under
# (``mcp__codex_apps__<tool>``), recorded as one server: caliper can't list the
# connectors behind it.
CODEX_APPS_SERVER = "codex_apps"


def _codex_entry(server: ResolvedMcpServer) -> dict:
    """One declared server in codex's ``mcp_servers`` shape.

    The common rendering, plus codex's one spelling difference: a remote
    server's ``headers`` map is written as ``http_headers``, static literal
    values per
    docs/adr/0011-codex-remote-mcp-uses-static-http-headers-not-env-indirection.md.
    Codex infers its one streamable-HTTP transport from ``url``, so caliper's
    ``type`` is dropped (remote OAuth, which caliper's spec cannot express, is
    out of reach).
    """
    entry = server.entry()
    if "headers" in entry:
        entry["http_headers"] = entry.pop("headers")
    return entry


# The top-level ``model`` is always dropped, so the seeded config never pins a
# model over the invocation (docs/adr/0012). Isolated, provider tables survive
# intact, endpoints and auth options included (docs/backends.md, docs/adr/0028).
CODEX_CONFIG = CliConfig(
    path=".codex/config.toml",
    codec=TOML,
    connection_keys=frozenset(
        {
            "model_provider",
            "model_providers",
            "cli_auth_credentials_store",
            "forced_login_method",
            "forced_chatgpt_workspace_id",
            "chatgpt_base_url",
            "openai_base_url",
        }
    ),
    dropped_keys=frozenset({"model"}),
    entry=_codex_entry,
)


class CodexHarness(CliHarness):
    def __init__(self, model: str | None = None) -> None:
        self._model = model

    supports_mcp = True
    supports_history = True
    cli_config = CODEX_CONFIG
    user_rules = ("AGENTS.md", "AGENTS.override.md")
    user_settings_file = "config.toml"
    cli_name = "codex"
    cli_path_env_var = "CODEX_CLI_PATH"
    cli_unavailable_message = (
        "Codex CLI is not available for the `codex` backend.\n\n"
        "Caliper runs skills only through CLI agents. Install and "
        "authenticate the Codex CLI to run with `--model codex`. For API "
        "billing, configure the Codex CLI with an API key rather than "
        "selecting a separate backend."
    )
    cli_version_timeout = 5

    @property
    def name(self) -> str:
        return "codex"

    def cli_candidates(self) -> tuple[Path, ...]:
        return (CODEX_APP_CLI,)

    def skills_root(self, ctx: RunContext) -> Path:
        return Path(ctx.isolated_home) / ".codex" / "skills"

    def seed_files(self, ctx: RunContext) -> list[tuple[Path, Path]]:
        real = Path.home() / ".codex"
        codex_home = Path(ctx.isolated_home) / ".codex"
        # config.toml is deliberately absent: it is rewritten rather than copied
        # (``cli_config``), so seeding it verbatim would leak the user's ambient
        # model pin and MCP servers into the attempt.
        return [(real / "auth.json", codex_home / "auth.json")]

    def _prepare(self, ctx: RunContext) -> None:
        real = Path.home() / ".codex"
        codex_home = Path(ctx.isolated_home) / ".codex"
        # Installed plugins, and the MCP servers they bring, live in this
        # directory rather than in config.toml; a copy keeps the user's own
        # untouched (docs/adr/0028).
        if ctx.user_customizations and (real / "plugins").is_dir():
            shutil.copytree(
                real / "plugins",
                codex_home / "plugins",
                symlinks=True,
                dirs_exist_ok=True,
            )
            self._repoint_escaping_links(real / "plugins", codex_home / "plugins")

    @staticmethod
    def _repoint_escaping_links(source: Path, copy: Path) -> None:
        """Make copied relative links that leave ``source`` absolute.

        Links are copied as links so dangling ones and loops cannot abort the
        copy, but a relative link out of the tree would dangle in the isolated
        home. Pointing it at the user's original target keeps it working.
        """
        for directory, dirnames, filenames in os.walk(copy):
            for name in [*dirnames, *filenames]:
                link = Path(directory) / name
                if not link.is_symlink():
                    continue
                target = os.readlink(link)
                if os.path.isabs(target):
                    continue
                original = source / link.parent.relative_to(copy)
                resolved = Path(os.path.normpath(original / target))
                if not resolved.is_relative_to(source):
                    link.unlink()
                    link.symlink_to(resolved)

    def _command(
        self, ctx: RunContext
    ) -> tuple[list[str], str | None, Callable[[], None] | None]:
        # The prompt goes to the agent unmodified. Codex has no force-load flag,
        # and caliper no longer invents one: the skill is installed at
        # .codex/skills/<name>/ and the agent discovers it (docs/adr/0013).
        full_prompt = ctx.prompt
        codex = self.cli_path() or "codex"
        cmd = [
            codex,
            "exec",
            "--json",
            "--skip-git-repo-check",
            "--dangerously-bypass-approvals-and-sandbox",
            "--color",
            "never",
            *self._connector_overrides(ctx),
            "-",
        ]
        if ctx.model:
            cmd[2:2] = ["--model", ctx.model]
        # The prompt becomes the next user turn after the planted ones. The
        # exec flags stay ahead of the subcommand, and the JSON stream does not
        # replay the planted turns, so they never reach the transcript.
        if ctx.history:
            cmd[-1:-1] = ["resume", self._write_rollout(ctx)]
        return cmd, full_prompt, None

    def _write_rollout(self, ctx: RunContext) -> str:
        """Write the planted ``ctx.history`` as a rollout codex can resume; return its id.

        Per attempt, in the attempt's own codex home (docs/adr/0036). Codex finds
        a resumed id by scanning ``sessions/``, so no index is written; the
        ``cwd`` is the attempt workdir, though resume by id doesn't check it.
        """
        now = datetime.now(timezone.utc)
        session_id = str(uuid.uuid4())
        sessions = Path(ctx.isolated_home) / ".codex" / "sessions"
        path = (
            sessions
            / now.strftime("%Y/%m/%d")
            / f"rollout-{now.strftime('%Y-%m-%dT%H-%M-%S')}-{session_id}.jsonl"
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = _rollout_lines(
            ctx.history or [],
            session_id=session_id,
            cwd=os.path.realpath(ctx.workdir),
            user_turn=lambda turn: self._user_turn(ctx, turn),
        )
        path.write_text("".join(json.dumps(line) + "\n" for line in lines))
        return session_id

    def _user_turn(self, ctx: RunContext, turn: PlantedTurn) -> list[str]:
        """A planted user turn's messages, as codex stores what was typed.

        ``/name`` of an installed skill is codex's ``$name`` mention, followed by
        the ``<skill>`` message codex injects, read from the installed copy so an
        edit to the skill reaches the next run. Anything else, including the
        command of an ablated skill, is the text as typed. The ``<skill>``
        message keeps the frontmatter, unlike claude-code's expansion: codex
        0.162.0 writes the whole file.
        """
        skill = self.planted_skill(ctx, turn)
        if skill is None:
            return [turn.user or ""]
        name, args, skill_md = skill
        return [
            f"${name} {args}".rstrip(),
            f"<skill>\n<name>{name}</name>\n<path>{skill_md}</path>\n"
            f"{skill_md.read_text()}\n</skill>",
        ]

    @staticmethod
    def _connector_overrides(ctx: RunContext) -> tuple[str, ...]:
        """The ``-c`` overrides that keep the account's connectors out, if any.

        All of them when isolated. When loading user customizations, only the
        hosted apps, and only if the spec declares a ``codex_apps`` of its own:
        the spec wins a name clash, ablated or not (docs/adr/0028).
        """
        if not ctx.user_customizations:
            return NO_ACCOUNT_CONNECTORS
        if CODEX_APPS_SERVER in ctx.spec_mcp_names:
            return ("-c", "features.apps=false")
        return ()

    def _environment(self, ctx: RunContext) -> dict[str, str]:
        return self._isolated_env(ctx)

    def _read(self, proc: ProcessResult, ctx: RunContext) -> AgentReport:
        """One walk of ``codex exec --json``'s events.

        Items carry the agent's turns; ``turn.completed`` its usage; ``error``
        and ``turn.failed`` events are codex itself failing, beside the agent.
        The MCP inventory is not in the stream: it is the ``config.toml`` the
        attempt ran with.
        """
        report = AgentReport()
        latest_usage: dict | None = None
        for event in stream_events(proc.stdout):
            error = _stream_error(event)
            if error:
                report.cli_errors.append(error)
            usage = event.get("usage")
            if event.get("type") == "turn.completed" and isinstance(usage, dict):
                latest_usage = usage
            item = event.get("item")
            if not isinstance(item, dict):
                continue
            if item.get("type") == "agent_message":
                text = item.get("text", "")
                if text:
                    report.transcript.append(
                        ConversationTurn(role="assistant", content=text)
                    )
                    report.final_output = text
            elif event.get("type") == "item.completed":
                report.transcript.extend(self._item_turns(item))

        if latest_usage is not None:
            report.read_usage = partial(_usage, latest_usage)
        report.read_mcp_servers = partial(_configured_servers, ctx)
        return report

    def _item_turns(self, item: dict) -> list[ConversationTurn]:
        """The turns one completed non-message item stands for."""
        if item.get("type") == "command_execution":
            command = item.get("command", "")
            output = item.get("aggregated_output", "")
            exit_code = item.get("exit_code")
            status = item.get("status")
            tool_input = {"command": command} if command else {}
            result_parts = []
            if output:
                result_parts.append(output)
            if exit_code is not None:
                result_parts.append(f"exit_code={exit_code}")
            if status:
                result_parts.append(f"status={status}")
            tool_output = "\n".join(result_parts)
            return [
                ConversationTurn(
                    role="tool_use",
                    content=f"[tool: shell] {command}",
                    tool_name="shell",
                    tool_input=tool_input,
                ),
                ConversationTurn(
                    role="tool_result",
                    content=tool_output,
                    tool_output=tool_output,
                ),
            ]

        if self._is_mcp_tool_call(item):
            return self._mcp_tool_turns(item)

        item_type = str(item.get("type") or "tool")
        return [
            ConversationTurn(
                role="tool_use",
                content=f"[tool: {item_type}]",
                tool_name=item_type,
                tool_input=item,
            )
        ]

    @staticmethod
    def _is_mcp_tool_call(item: dict) -> bool:
        """True when a completed item is an MCP tool invocation we can name.

        Detected *structurally* — a ``server`` plus a ``tool``/``tool_name`` (the
        fields codex's ``McpToolCall`` carries) — rather than by the type label, so
        the qualified name is recoverable across codex builds. An ``mcp_tool_call``
        item lacking those fields falls through to the generic tool branch.
        """
        tool = item.get("tool") or item.get("tool_name")
        return bool(item.get("server") and tool)

    def _mcp_tool_turns(self, item: dict) -> list[ConversationTurn]:
        """Render an MCP tool call as claude-code-parity ``mcp__<server>__<tool>``.

        Emits a ``tool_use`` turn named ``mcp__<server>__<tool>`` (the doubled-
        underscore form codex shares with claude-code) so a backend-agnostic
        ``expect:``/``assert:`` on that name matches, plus a ``tool_result`` turn
        carrying any output/error/status the item reported.
        """
        server = item.get("server")
        tool = item.get("tool") or item.get("tool_name")
        qualified = mcp_tool_name(self.name, server, tool)
        args = item.get("arguments")
        turns = [
            ConversationTurn(
                role="tool_use",
                content=f"[tool: {qualified}]",
                tool_name=qualified,
                tool_input=args if isinstance(args, dict) else item,
            )
        ]

        result = item.get("result")
        if result is None:
            result = item.get("output")
        parts: list[str] = []
        if result is not None:
            parts.append(result if isinstance(result, str) else json.dumps(result))
        error = item.get("error")
        if error:
            parts.append(error if isinstance(error, str) else json.dumps(error))
        status = item.get("status")
        if status:
            parts.append(f"status={status}")
        if parts:
            output = "\n".join(parts)
            turns.append(
                ConversationTurn(role="tool_result", content=output, tool_output=output)
            )
        return turns

    # --- bare prompt call (the judge's half of the seam) -------------------

    def _prompt_command(self, prompt: str, model: str | None) -> PromptCall:
        codex = self.cli_path()
        if not codex:
            raise HarnessConfigurationError("codex CLI not found")

        # `--output-last-message` writes the final answer to a file, which is
        # the only clean channel: codex's stdout is a noisy session log. The
        # file outlives the process so the reader can pick it up; the reader
        # closes over its path, and ``cleanup`` removes it however the call
        # ended — a timeout never reaches the reader.
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as output_file:
            output_path = Path(output_file.name)

        cmd = [
            codex,
            "exec",
            "--skip-git-repo-check",
            "--dangerously-bypass-approvals-and-sandbox",
            "--color",
            "never",
            "--output-last-message",
            str(output_path),
            *NO_ACCOUNT_CONNECTORS,
            "-",
        ]
        if model:
            cmd[2:2] = ["--model", model]
        return PromptCall(
            cmd,
            stdin=prompt,
            read=lambda proc: self._read_last_message(proc, output_path, prompt),
            cleanup=lambda: output_path.unlink(missing_ok=True),
        )

    def _prompt_cli_text(self, proc: ProcessResult, report: AgentReport) -> str:
        # Only the error line the reader picked out: `codex exec` echoes the
        # whole judge prompt to stderr, and the graded answer can say "401".
        return "\n".join(report.cli_errors)

    def _read_last_message(
        self, proc: ProcessResult, output_path: Path, prompt: str
    ) -> AgentReport:
        raw = (
            output_path.read_text(encoding="utf-8").strip()
            if output_path.exists()
            else ""
        )
        raw = raw or proc.stdout.strip()
        if proc.returncode != 0:
            # codex echoes the prompt to stderr verbatim, and the graded answer
            # inside it may hold an error line of its own.
            stderr = proc.stderr.replace(prompt.strip(), "")
            detail = _extract_codex_error(stderr) or _extract_codex_error(raw)
            return AgentReport(cli_errors=[detail] if detail else [])
        # Codex doesn't surface the resolved model in this mode, so the caller
        # reports the one that was requested (None when its own default ran).
        if not raw:
            return AgentReport()
        return AgentReport(transcript=[ConversationTurn("assistant", raw)])

    config_signals = (
        ConfigSignal(
            (
                *AUTH_MARKERS,
                "401 unauthorized",
                "api key",
                "chatgpt account",
                "sign in again",
            ),
            "Codex CLI cannot run with the current subscription/authentication "
            "configuration.\n\n"
            "Caliper uses `codex exec` for `--model codex` and does not fall "
            "back to the OpenAI API. The Codex CLI returned:\n"
            "  {text}\n\n"
            "Run `{login}` and verify `codex exec` works in your normal "
            "shell, then retry the eval. For API billing, configure the "
            "Codex CLI with an API key rather than selecting a separate "
            "backend.",
            login=True,
        ),
    )

    def login_command(self) -> list[str]:
        return [self.cli_path() or "codex", "login"]

    def _diagnose(
        self, proc: ProcessResult, report: AgentReport, cli_text: str
    ) -> str | None:
        lowered = cli_text.lower()
        model_markers = (
            "requires a newer version of codex",
            "please upgrade to the latest app or cli",
            "model is not supported",
            "model is not available",
        )
        if any(marker in lowered for marker in model_markers):
            summary = self._summarize_cli_configuration_error(cli_text)
            return (
                "Codex CLI cannot run the requested model with this account or "
                "installed version.\n\n"
                "Caliper uses `codex exec` for `--model codex`. The Codex CLI "
                "returned:\n"
                f"  {summary}\n\n"
                "Pass `--model codex` (no model) to use the Codex CLI default, "
                "upgrade the Codex app "
                "or CLI, or choose a model supported by the installed Codex CLI "
                "and account, then retry the eval."
            )

        if (
            sys.platform == "darwin"
            and proc.returncode != 0
            and "operation not permitted" in lowered
        ):
            return (
                "Codex CLI was blocked by the operating system while running under "
                "Caliper.\n\n"
                f"The Codex CLI returned:\n  {cli_text}"
            )

        return None

    def _summarize_cli_configuration_error(self, text: str) -> str:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        version_line = next(
            (line for line in lines if line.startswith("OpenAI Codex")), None
        )
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


def _rollout_lines(
    history: list[PlantedTurn],
    *,
    session_id: str,
    cwd: str,
    user_turn: Callable[[PlantedTurn], list[str]],
) -> list[dict]:
    """``history`` as codex rollout lines: the ``session_meta``, then one item per line.

    Only the fields a resume was seen to need (0.162.0). Codex refuses a
    rollout whose meta lacks any of its keys, but doesn't check the version or
    provider recorded: the resumed turn runs on the attempt's own config. A
    planted shell call is an ``exec_command`` function call, answered by the
    output with its ``call_id``; ``user_turn`` writes a user turn's messages.
    """
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    meta = {
        "id": session_id,
        "session_id": session_id,
        "timestamp": timestamp,
        "cwd": cwd,
        "originator": "caliper",
        "cli_version": "",
        "source": "exec",
        "model_provider": "openai",
    }

    def message(role: str, text: str) -> dict:
        kind = "input_text" if role == "user" else "output_text"
        return {
            "type": "message",
            "role": role,
            "content": [{"type": kind, "text": text}],
        }

    items: list[dict] = []
    for turn in history:
        if turn.user is not None:
            items += [message("user", text) for text in user_turn(turn)]
            continue
        if turn.assistant:
            items.append(message("assistant", turn.assistant))
        for call in turn.shell:
            call_id = f"call_planted_{len(items)}"
            items += [
                {
                    "type": "function_call",
                    "name": "exec_command",
                    "arguments": json.dumps({"cmd": call.run}),
                    "call_id": call_id,
                },
                {
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": call.output,
                },
            ]
    return [
        {"timestamp": timestamp, "type": "session_meta", "payload": meta},
        *(
            {"timestamp": timestamp, "type": "response_item", "payload": item}
            for item in items
        ),
    ]


def _usage(usage: dict) -> TokenUsage:
    """Map the last ``turn.completed`` event's ``usage``.

    Codex uses OpenAI semantics where ``input_tokens`` *includes*
    ``cached_input_tokens``, so we subtract to keep ``input_tokens``
    non-cached (the disjoint-fields contract). Codex has no cache-creation
    notion, and ``reasoning_output_tokens`` is already folded into
    ``output_tokens``.
    """
    raw_input = usage.get("input_tokens")
    cached = usage.get("cached_input_tokens")
    non_cached = None
    if raw_input is not None:
        non_cached = raw_input - (cached or 0)
    return TokenUsage(
        input_tokens=non_cached,
        output_tokens=usage.get("output_tokens"),
        cache_read_tokens=cached,
        cache_creation_tokens=None,
    )


def _stream_error(event: dict) -> str | None:
    """The message of a failure event codex wrote into its JSON stream, if any."""
    if event.get("type") not in ("error", "turn.failed") and not isinstance(
        event.get("error"), dict
    ):
        return None
    message = event.get("message")
    error = event.get("error")
    if not isinstance(message, str) and isinstance(error, dict):
        message = error.get("message")
    if isinstance(message, str) and message.strip():
        return message.strip()
    return None


def _configured_servers(ctx: RunContext) -> set[str] | None:
    """The servers in the ``config.toml`` the attempt ran with, plus hosted apps.

    The hosted apps surface as one server, ``codex_apps``, and only for a
    ChatGPT login whose config leaves ``apps`` on. Such a login's plugins
    bring tools caliper can't list, so with plugins on the set is unknown.
    """
    config = CODEX_CONFIG.read(ctx)
    names = CODEX_CONFIG.servers(ctx)
    features = config.get("features")
    features = features if isinstance(features, dict) else {}
    if _chatgpt_login(Path(ctx.isolated_home) / ".codex" / "auth.json"):
        if features.get("plugins") is not False:
            return None
        if features.get("apps") is not False:
            names.add(CODEX_APPS_SERVER)
    return names


def _extract_codex_error(output: str) -> str | None:
    """Pull a readable failure message out of codex's noisy CLI output."""
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
    if not isinstance(payload, dict):
        return None

    error = payload.get("error")
    if isinstance(error, dict):
        message = error.get("message")
        if isinstance(message, str) and message:
            return message
    return None


def _chatgpt_login(auth_path: Path) -> bool:
    """True when ``auth.json`` holds a ChatGPT login, the only kind with apps.

    A ChatGPT login stores OAuth ``tokens``; an API-key login does not, and
    brings no hosted connectors. Unreadable or absent counts as no login, so a
    connector set caliper cannot vouch for is never recorded.
    """
    try:
        auth = json.loads(auth_path.read_text())
    except (OSError, ValueError):
        return False
    return isinstance(auth, dict) and bool(auth.get("tokens"))
