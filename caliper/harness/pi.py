from __future__ import annotations

import json
import os
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
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
from caliper.harness.refusal import AUTH_MARKERS, ConfigSignal
from caliper.schema.results import TokenUsage
from caliper.schema.spec import PlantedTurn
from caliper.skills import skill_body

_PROVIDER_DIAGNOSIS = (
    "pi cannot run with the current provider/credential "
    "configuration.\n\n"
    "Caliper copies your `~/.pi/agent` auth and settings verbatim "
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
        # Its `!command` and `$NAME` credentials run in the isolated env, so
        # home-relative or env-based ones don't resolve (docs/backends.md → pi).
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
            ["--session", str(self._write_session(ctx))]
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
        cmd.append(ctx.prompt)
        # stdin is left as None so the template closes it (DEVNULL): in --print
        # mode pi otherwise blocks reading stdin (e.g. for a trust confirmation)
        # and hangs until timeout.
        return cmd, None, None

    def _write_session(self, ctx: RunContext) -> Path:
        """Write the planted ``ctx.history`` as a session pi can resume; return its path.

        Per attempt, in the attempt's own agent dir (docs/adr/0036), and under
        ``sessions/``: pi 0.87.1 moves a session file it finds in the agent dir's
        root. pi appends the resumed turns to this same file.
        """
        now = datetime.now(timezone.utc)
        session_id = str(uuid.uuid4())
        sessions = self._agent_dir(ctx) / "sessions"
        path = sessions / f"{now.strftime('%Y-%m-%dT%H-%M-%S')}_{session_id}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = _session_lines(
            ctx.history or [],
            session_id=session_id,
            cwd=os.path.realpath(ctx.workdir),
            user_turn=lambda turn: self._user_turn(ctx, turn),
        )
        path.write_text("".join(json.dumps(line) + "\n" for line in lines))
        return path

    def _user_turn(self, ctx: RunContext, turn: PlantedTurn) -> str:
        """A planted user turn as pi would have stored it.

        ``/name`` of an installed skill is what pi expands ``/skill:name`` to,
        read from the installed copy so an edit to the skill reaches the next
        run. Anything else, including the command of an ablated skill, is the
        text as typed.
        """
        skill = self.planted_skill(ctx, turn)
        if skill is None:
            return turn.user or ""
        name, args, skill_md = skill
        block = (
            f'<skill name="{name}" location="{skill_md}">\n'
            f"References are relative to {skill_md.parent}.\n\n"
            f"{skill_body(skill_md.read_text())}\n</skill>"
        )
        return f"{block}\n\n{args}" if args else block

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
        cmd.append(prompt)
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
            "Caliper drives the local pi CLI and reuses its `~/.pi/agent` "
            "credentials. The pi CLI returned:\n"
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


def _session_lines(
    history: list[PlantedTurn],
    *,
    session_id: str,
    cwd: str,
    user_turn: Callable[[PlantedTurn], str],
) -> list[dict]:
    """``history`` as pi session lines: the ``session`` header, then one message per line.

    Only the fields a resume was seen to need (0.87.1). Messages chain by
    ``id``/``parentId``. A planted assistant turn names no real model: pi
    resumes on the attempt's own, whatever a turn records. A planted shell
    call is a ``bash`` tool call, answered by the ``toolResult`` with its id;
    ``user_turn`` writes a user turn's text.
    """
    now = datetime.now(timezone.utc)
    timestamp = now.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    millis = int(now.timestamp() * 1000)
    header = {
        "type": "session",
        "version": 3,
        "id": session_id,
        "timestamp": timestamp,
        "cwd": cwd,
    }
    entries: list[dict] = []
    usage = {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0, "totalTokens": 0}

    def add(message: dict) -> None:
        entries.append(
            {
                "type": "message",
                "id": uuid.uuid4().hex[:8],
                "parentId": entries[-1]["id"] if entries else None,
                "timestamp": timestamp,
                "message": {**message, "timestamp": millis},
            }
        )

    def text(value: str) -> dict:
        return {"type": "text", "text": value}

    for turn in history:
        if turn.user is not None:
            add({"role": "user", "content": [text(user_turn(turn))]})
            continue
        calls = [
            (f"call_planted_{len(entries)}_{i}", c) for i, c in enumerate(turn.shell)
        ]
        content = [text(turn.assistant)] if turn.assistant else []
        content += [
            {
                "type": "toolCall",
                "id": call_id,
                "name": "bash",
                "arguments": {"command": call.run},
            }
            for call_id, call in calls
        ]
        add(
            {
                "role": "assistant",
                "content": content,
                "api": "caliper",
                "provider": "caliper",
                "model": "planted",
                "usage": usage,
                "stopReason": "toolUse" if calls else "stop",
            }
        )
        for call_id, call in calls:
            add(
                {
                    "role": "toolResult",
                    "toolCallId": call_id,
                    "toolName": "bash",
                    "content": [text(call.output)],
                    "isError": False,
                }
            )
    return [header, *entries]


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
