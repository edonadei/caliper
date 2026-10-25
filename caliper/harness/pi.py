from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import uuid
from collections.abc import Callable
from functools import partial
from pathlib import Path

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
from caliper.harness.history import PlantedSkill, Session, SessionFormat
from caliper.harness.refusal import AUTH_MARKERS, ConfigSignal
from caliper.schema.results import TokenUsage
from caliper.schema.spec import PlantedShellCall

_PROVIDER_DIAGNOSIS = (
    "pi cannot run with the current provider/credential "
    "configuration.\n\n"
    "Caliper copies your pi agent dir (`$PI_CODING_AGENT_DIR`, else `~/.pi/agent`) auth and settings verbatim "
    "and passes a model to pi only when you select one. The pi CLI "
    "returned:\n"
    "  {text}\n\n"
    "pi's built-in default provider is `google`, so running "
    "`--model pi` with no model (and no Google credentials) can fail "
    "here. Pass `--model pi:<model>` for a provider you are "
    "authenticated for, or configure pi's default provider with `pi` "
    "directly, then rerun caliper."
)


class PiHarness(CliHarness):
    """CLI-subprocess backend for the `pi` coding agent.

    Drives the locally installed `pi` CLI in non-interactive JSON mode and
    loads the skill-under-test natively via pi's `--skill` flag (the
    agentskills.io standard), unlike codex which injects the skill into the
    prompt.
    """

    def __init__(self, model: str | None = None) -> None:
        self._model = model

    # pi has no MCP support, and this is a deliberate stance of the tool (its
    # README: "No MCP. Build a CLI tool with a README, or an extension that adds
    # MCP support."), not a slice caliper hasn't wired yet. So ``supports_mcp``
    # stays False and the refusal carries a permanent-by-design hint that points
    # the spec author at pi's own escape hatch rather than implying a later slice.
    mcp_unsupported_hint = (
        "The pi agent has no MCP support, and this is by design — pi will not "
        "honor an mcp: block natively. Instead of MCP, expose the same "
        "capability as a CLI tool the skill drives (a skill with a README), or "
        "build a pi extension that adds it. To measure MCP-based skills under "
        "caliper, run them on the claude-code backend (--model claude-code)."
    )

    supports_history = True

    cli_name = "pi"
    cli_path_env_var = "PI_CLI_PATH"
    config_home_env = "PI_CODING_AGENT_DIR"
    config_home = ".pi/agent"
    cli_unavailable_message = (
        "pi CLI is not available for the `pi` backend.\n\n"
        "Install the pi coding agent (`npm install -g "
        "@earendil-works/pi-coding-agent`) and authenticate it, or set "
        "`PI_CLI_PATH` to the pi binary, then rerun caliper."
    )

    @property
    def name(self) -> str:
        return "pi"

    def seed_files(self, ctx: RunContext) -> list[tuple[Path, Path]]:
        # pi reads auth/settings from its config dir. The real config is copied
        # verbatim into a per-attempt directory (parallel-safe; never mutates
        # the user's real ~/.pi) that PI_CODING_AGENT_DIR then points pi at.
        # The config's default model/provider is preserved on purpose; the
        # spec's `--model` overrides it when set. See issue #10 for why this
        # differs from codex (which strips its config default). models.json
        # defines custom providers (Ollama, proxies, any compatible baseUrl), so
        # a model that resolves by hand must resolve in the attempt too (#178).
        # Its `!command` and `$NAME` credentials are resolved on the host in
        # `_prepare`, once the copy is in place (docs/adr/0040).
        real = self.user_config_home()
        agent_dir = self._agent_dir(ctx)
        return [
            (real / name, agent_dir / name)
            for name in ("auth.json", "settings.json", "models.json")
        ]

    @staticmethod
    def _agent_dir(ctx: RunContext) -> Path:
        """The per-attempt config dir pi is pointed at via PI_CODING_AGENT_DIR.

        Derived from the isolated home rather than stashed for a later hook to
        find: it is a function of the context, so there is nothing to remember
        between them (docs/adr/0024-a-seam-carries-only-what-its-readers-use.md).
        """
        return Path(ctx.isolated_home) / ".pi" / "agent"

    def skills_root(self, ctx: RunContext) -> Path:
        return self._agent_dir(ctx) / "skills"

    def _command(
        self, ctx: RunContext
    ) -> tuple[list[str], str | None, Callable[[], None] | None]:
        pi = self.cli_path() or "pi"
        # The prompt becomes the next user turn after the planted ones. The JSON
        # stream does not replay them, so they never reach the transcript.
        session = (
            ["--session", str(PI_SESSION.plant(ctx, self.skills_root(ctx)).path)]
            if ctx.history
            else ["--no-session"]
        )
        cmd = [
            pi,
            "--print",
            "--mode",
            "json",
            *session,
            # Trust the project-local skill file for this run; without it pi
            # blocks on an interactive trust prompt when a skill is loaded.
            "--approve",
        ]
        if ctx.model:
            cmd += ["--model", ctx.model]
        # No --skill: that flag preloads, and pi's own --no-skills exists
        # precisely because discovery is its default. The neighbourhood is
        # installed under the agent dir and pi finds it (docs/adr/0013).
        # `--` ends option parsing, so a prompt like `--version` is the task.
        cmd += ["--", ctx.prompt]
        # stdin is left as None so the template closes it (DEVNULL): in --print
        # mode pi otherwise blocks reading stdin (e.g. for a trust confirmation)
        # and hangs until timeout.
        return cmd, None, None

    def _prepare(self, ctx: RunContext) -> None:
        _resolve_credentials(self._agent_dir(ctx) / "models.json")

    def _environment(self, ctx: RunContext) -> dict[str, str]:
        return self._isolated_env(
            ctx, extra={"PI_CODING_AGENT_DIR": str(self._agent_dir(ctx))}
        )

    def _read(self, proc: ProcessResult, ctx: RunContext) -> AgentReport:
        return self._read_stream(proc.stdout)

    def _read_stream(self, stdout: str) -> AgentReport:
        """One walk of pi's JSON event stream.

        Tool executions and finished assistant messages are the turns. Each
        assistant ``message_end`` carries its own turn's usage, summed into the
        run total. In --mode json pi exits 0 even when it never reached the
        model, and reports why only as an errored assistant message (#132):
        that message is the CLI talking, and an answer that mentions "401" is
        not a broken login.
        """
        report = AgentReport()
        usages: list[dict] = []

        for event in stream_events(stdout):
            etype = event.get("type")

            if etype == "tool_execution_start":
                tool_name = event.get("toolName")
                args = event.get("args")
                report.transcript.append(
                    ConversationTurn(
                        role="tool_use",
                        content=f"[tool: {tool_name}]",
                        tool_name=tool_name,
                        tool_input=args if isinstance(args, dict) else None,
                    )
                )
                continue

            if etype == "tool_execution_end":
                output = self._flatten_result(event.get("result"))
                report.transcript.append(
                    ConversationTurn(
                        role="tool_result",
                        content=output,
                        tool_output=output,
                    )
                )
                continue

            if etype == "message_end":
                message = event.get("message")
                if not isinstance(message, dict) or message.get("role") != "assistant":
                    continue
                if isinstance(message.get("usage"), dict):
                    usages.append(message["usage"])
                error = _stream_error(message)
                if error is not None:
                    report.cli_errors.append(error)
                text = self._flatten_text(message.get("content"))
                if text:
                    report.transcript.append(
                        ConversationTurn(role="assistant", content=text)
                    )
                    report.final_output = text

        if usages:
            report.read_usage = partial(_usage, usages)
        return report

    def _flatten_text(self, content: object) -> str:
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

    def _flatten_result(self, result: object) -> str:
        if isinstance(result, dict):
            return self._flatten_text(result.get("content"))
        if isinstance(result, str):
            return result
        return ""

    # --- bare prompt call (the judge's half of the seam) -------------------

    def _prompt_command(self, prompt: str, model: str | None) -> PromptCall:
        pi = self.cli_path()
        if not pi:
            raise HarnessConfigurationError("pi CLI not found")

        cmd = [pi, "--print", "--mode", "json", "--no-session", "--approve"]
        if model:
            cmd += ["--model", model]
        cmd += ["--", prompt]
        # stdin None → the template closes it (DEVNULL): in --print mode pi
        # otherwise blocks reading stdin and hangs until timeout.
        return PromptCall(cmd)

    def _read_prompt(self, proc: ProcessResult) -> AgentReport:
        # The same JSON event stream an attempt run reads, tail and all. pi
        # exits 0 when it never reached the model, and says why only as an
        # errored message in its stream (#132).
        return self._read_stream(proc.stdout)

    config_signals = (
        ConfigSignal(
            (
                "no api key",
                "no credentials",
                "missing api key",
                "set an api key",
                "no provider",
                "unknown provider",
            ),
            _PROVIDER_DIAGNOSIS,
        ),
        ConfigSignal(
            (*AUTH_MARKERS, "oauth refresh failed", "invalid_grant", "refresh token"),
            "pi cannot run with the current authentication "
            "configuration.\n\n"
            "Caliper drives the local pi CLI and reuses the "
            "credentials in its agent dir (`$PI_CODING_AGENT_DIR`, else `~/.pi/agent`). The pi CLI returned:\n"
            "  {text}\n\n"
            "Authenticate pi (e.g. `pi` then `/login`, or set the provider "
            "API key), verify `pi --print 'Reply OK'` works in your normal "
            "shell, then rerun caliper.",
            login=True,
        ),
    )

    def _diagnose(
        self, proc: ProcessResult, report: AgentReport, cli_text: str
    ) -> str | None:
        lowered = cli_text.lower()
        if "provider" in lowered and "api key" in lowered:
            return _PROVIDER_DIAGNOSIS.replace("{text}", cli_text)
        return None


def _resolve_credentials(models_json: Path) -> None:
    """Resolve the seeded models.json's credentials on the host, in place.

    pi resolves ``!command`` and ``$NAME`` values in a provider's ``apiKey`` and
    in every ``headers`` table when it makes a request, which in an attempt is
    inside the isolated env. Resolving them here gives the attempt the values
    the operator's own pi would use (docs/adr/0040). Anything the host can't
    resolve, and any file pi itself would reject, is left as written.
    """
    try:
        # utf-8-sig: pi strips a BOM before parsing, and Windows editors write one.
        models = json.loads(models_json.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return
    changed = False
    for provider in _dict_members(_member(models, "providers")):
        changed |= _resolve_field(provider, "apiKey")
        tables = [provider, *_dict_members(_member(provider, "models"))]
        tables += _dict_members(_member(provider, "modelOverrides"))
        for headers in [_member(table, "headers") for table in tables]:
            if isinstance(headers, dict):
                for name in list(headers):
                    changed |= _resolve_field(headers, name)
    if changed:
        models_json.write_text(json.dumps(models, indent=2), encoding="utf-8")
        # It now holds resolved secrets (docs/adr/0040).
        models_json.chmod(0o600)


def _member(table: object, key: str) -> object:
    """``table[key]`` when ``table`` is a JSON object, else ``None``."""
    return table.get(key) if isinstance(table, dict) else None


def _dict_members(container: object) -> list[dict]:
    """The objects in a JSON array, or among a JSON object's values."""
    if isinstance(container, dict):
        container = list(container.values())
    if not isinstance(container, list):
        return []
    return [value for value in container if isinstance(value, dict)]


def _resolve_field(table: dict, name: str) -> bool:
    value = table.get(name)
    if not isinstance(value, str):
        return False
    if value.startswith("!"):
        resolved = _run_credential_command(value[1:])
    else:
        resolved = _interpolate(value)
    if resolved is None:
        return False
    # pi parses the written value again, so it must read back literally.
    escaped = resolved.replace("$", "$$")
    table[name] = "$" + escaped if escaped.startswith("!") else escaped
    return True


_ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _interpolate(value: str) -> str | None:
    """``value`` with its ``$NAME`` / ``${NAME}`` references filled from the host.

    Parsed the way pi parses it, ``$$`` and ``$!`` escapes included. ``None``
    when there is no reference (a literal is left exactly as written) or when a
    referenced variable is unset or empty, since pi treats both as missing.
    """
    out = []
    i = 0
    referenced = False
    while (dollar := value.find("$", i)) >= 0:
        out.append(value[i:dollar])
        rest = value[dollar + 1 :]
        if rest[:1] in ("$", "!"):
            out.append(rest[0])
            i = dollar + 2
            continue
        if rest.startswith("{") and (end := rest.find("}")) >= 0:
            name, i = rest[1:end], dollar + end + 2
            if not _ENV_NAME.fullmatch(name):
                out.append(value[dollar:i])
                continue
        elif match := _ENV_NAME.match(rest):
            name, i = match[0], dollar + 1 + match.end()
        else:
            out.append("$")
            i = dollar + 1
            continue
        if not os.environ.get(name):
            return None
        out.append(os.environ[name])
        referenced = True
    out.append(value[i:])
    return "".join(out) if referenced else None


def _run_credential_command(command: str) -> str | None:
    """Run a ``!command`` the way pi does: its stdout, trimmed, if it succeeds.

    pi uses ``sh`` on POSIX. On Windows it prefers Git Bash, then a
    ``bash.exe`` on ``PATH`` (legacy WSL bash reads the command from stdin),
    and only then the default shell, which it also falls back to when bash
    cannot start. Same 10s limit as pi, and the same lenient UTF-8 decoding.
    """
    bash = _windows_bash() if sys.platform == "win32" else None
    if bash is not None:
        legacy_wsl = re.fullmatch(
            r"[a-z]:\\windows\\(system32|sysnative)\\bash\.exe", bash.lower()
        )
        try:
            if legacy_wsl:
                return _command_output([bash, "-s"], input=command)
            return _command_output([bash, "-c", command], stdin=subprocess.DEVNULL)
        except OSError:
            pass
    try:
        return _command_output(command, shell=True, stdin=subprocess.DEVNULL)
    except OSError:
        return None


def _command_output(cmd: str | list[str], **how: object) -> str | None:
    """Trimmed stdout of ``cmd`` when it exits 0 and prints something, else ``None``."""
    try:
        proc = subprocess.run(
            cmd,
            **how,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
    except subprocess.TimeoutExpired:
        return None
    return (proc.stdout.strip() or None) if proc.returncode == 0 else None


def _windows_bash() -> str | None:
    for root in (os.environ.get("PROGRAMFILES"), os.environ.get("PROGRAMFILES(X86)")):
        if root and (candidate := Path(root) / "Git" / "bin" / "bash.exe").is_file():
            return str(candidate)
    return shutil.which("bash.exe")


def _text(value: str) -> dict:
    return {"type": "text", "text": value}


# A planted assistant turn's usage: it cost the attempt nothing.
_NO_USAGE = {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0, "totalTokens": 0}


class _PiSession(SessionFormat):
    """A pi session: the ``session`` header, then one message per line.

    Fields as a resume was seen to need them (0.87.1). Messages chain by
    ``id``/``parentId``. The file goes under the agent dir's ``sessions/``:
    pi moves a session file it finds in the agent dir's root, and appends the
    resumed turns to this same file. A planted assistant turn names no real
    model: pi resumes on the attempt's own, whatever a turn records. A planted
    shell call is a ``bash`` tool call, answered by the ``toolResult`` with its
    id.
    """

    def path(self, session: Session) -> Path:
        stamp = session.now.strftime("%Y-%m-%dT%H-%M-%S")
        return session.config_dir / "sessions" / f"{stamp}_{session.id}.jsonl"

    def header(self, session: Session) -> dict:
        return {
            "type": "session",
            "version": 3,
            "id": session.id,
            "timestamp": session.timestamp,
            "cwd": session.cwd,
        }

    def user(self, text: str) -> list[dict]:
        return [{"role": "user", "content": [_text(text)]}]

    def skill(self, skill: PlantedSkill) -> list[dict]:
        """What pi expands ``/skill:name`` to."""
        block = (
            f'<skill name="{skill.name}" location="{skill.skill_md}">\n'
            f"References are relative to {skill.skill_md.parent}.\n\n"
            f"{skill.body}\n</skill>"
        )
        return self.user(f"{block}\n\n{skill.args}" if skill.args else block)

    def assistant(
        self, text: str | None, calls: list[tuple[str, PlantedShellCall]]
    ) -> list[dict]:
        content = [_text(text)] if text else []
        content += [
            {
                "type": "toolCall",
                "id": call_id,
                "name": "bash",
                "arguments": {"command": call.run},
            }
            for call_id, call in calls
        ]
        said = {
            "role": "assistant",
            "content": content,
            "api": "caliper",
            "provider": "caliper",
            "model": "planted",
            "usage": _NO_USAGE,
            "stopReason": "toolUse" if calls else "stop",
        }
        return [
            said,
            *(
                {
                    "role": "toolResult",
                    "toolCallId": call_id,
                    "toolName": "bash",
                    "content": [_text(call.output)],
                    "isError": False,
                }
                for call_id, call in calls
            ),
        ]

    def envelope(self, message: dict, previous: dict | None, session: Session) -> dict:
        return {
            "type": "message",
            "id": uuid.uuid4().hex[:8],
            "parentId": previous["id"] if previous else None,
            "timestamp": session.timestamp,
            "message": {**message, "timestamp": int(session.now.timestamp() * 1000)},
        }


PI_SESSION = _PiSession()


def _usage(usages: list[dict]) -> TokenUsage:
    """Sum the assistant messages' per-turn usage into the run total.

    pi's ``input`` is already non-cached, so ``cacheRead``/``cacheWrite`` map
    straight onto the disjoint cache fields.
    """
    totals = {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0}
    for usage in usages:
        for key in totals:
            value = usage.get(key)
            if isinstance(value, int):
                totals[key] += value
    return TokenUsage(
        input_tokens=totals["input"],
        output_tokens=totals["output"],
        cache_read_tokens=totals["cacheRead"],
        cache_creation_tokens=totals["cacheWrite"],
    )


def _stream_error(message: dict) -> str | None:
    """The ``errorMessage`` of an assistant turn pi ended on an error, if any.

    Trimmed at pi's ``; stack=`` suffix: the Node stack trace says where pi
    failed, which is no help to whoever has to fix their login.
    """
    error = message.get("errorMessage")
    if message.get("stopReason") != "error" or not isinstance(error, str):
        return None
    return error.split("; stack=", 1)[0].strip()
