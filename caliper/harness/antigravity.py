from __future__ import annotations

import json
import os
import sys
from collections.abc import Callable
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
from caliper.harness.cli_config import JSON, CliConfig
from caliper.harness.history import PlantedSession
from caliper.harness.refusal import AUTH_MARKERS, ConfigSignal
from caliper.schema.results import TokenUsage

# agy reads MCP servers from ``config/mcp_config.json`` under ``~/.gemini``,
# which holds nothing else, and which the Antigravity app shares. It takes the
# common server rendering unchanged (``url`` is its alias for ``serverUrl``)
# and speaks only Streamable HTTP to a remote, so ``sse`` collapses onto it as
# on codex.
AGY_MCP_CONFIG = CliConfig(
    path="config/mcp_config.json",
    codec=JSON,
    connection_keys=frozenset(),
    servers_key="mcpServers",
)

# A background self-update in an attempt or a judge call would rewrite the
# binary other attempts of the run are spawning.
_NO_AUTOUPDATE = {"AGY_CLI_DISABLE_AUTO_UPDATE": "1"}


def _windows_roaming(home: str | Path) -> Path:
    return Path(home) / "AppData" / "Roaming"


class AntigravityHarness(CliHarness):
    """CLI-subprocess backend for Google's Antigravity CLI (`agy`).

    Drives `agy` in print mode with its stream-json output. The prompt goes in
    on stdin as one stream-json ``user`` message rather than as ``--print``'s
    value: agy refuses a flag-shaped ``--print`` value outright, and stdin
    takes any text as the task. Skills are installed where agy discovers them
    (``~/.gemini/antigravity-cli/skills``) and left to be found
    (docs/adr/0013).
    """

    def __init__(self, model: str | None = None, effort: str | None = None) -> None:
        self._model = model
        self._effort = effort

    # stdout is only the event stream: one that did not parse is no answer.
    salvages_raw_stdout = False

    supports_mcp = True
    cli_config = AGY_MCP_CONFIG
    # agy keeps conversations as protobuf steps in SQLite, written only by
    # agy itself, so a planted history has no faithful way in (docs/adr/0036).
    history_unsupported_hint = (
        "Antigravity cannot resume planted turns: agy stores conversations "
        "only in its own SQLite format, with no import. Run tasks with "
        "history: on --model claude-code, --model codex, --model pi or "
        "--model opencode, or remove history: to run on antigravity."
    )

    cli_name = "agy"
    cli_path_env_var = "AGY_CLI_PATH"
    # agy reads no variable for its config home, so the attempt's isolated
    # ``HOME`` is what points it at the attempt's own.
    config_home = ".gemini"
    # The global rules agy loads, cumulatively; ``config/rules/*.md`` is a
    # directory the user layer does not stage.
    user_rules = ("AGENTS.md", "GEMINI.md", "config/AGENTS.md", "config/GEMINI.md")
    user_settings_file = "antigravity-cli/settings.json"
    # agy scans only immediate child skill folders; nested folders are skipped.
    recursive_user_skills = False
    cli_unavailable_message = (
        "Antigravity CLI is not available for the `antigravity` backend.\n\n"
        "Install it (`curl -fsSL https://antigravity.google/cli/install.sh | "
        "bash`), run `agy` once to sign in, or set `AGY_CLI_PATH` to the agy "
        "binary, then rerun caliper."
    )

    # The login lives in the OS keyring, which the isolated ``HOME`` does not
    # move on Linux (Secret Service, over the session bus) or Windows. An API
    # key or ADC login is read from the environment instead.
    env_passthrough = CliHarness.env_passthrough + (
        "DBUS_SESSION_BUS_ADDRESS",
        "XDG_RUNTIME_DIR",
        "GEMINI_API_KEY",
        "GOOGLE_GEMINI_BASE_URL",
        "AGY_ADC_AUTH",
        "GOOGLE_APPLICATION_CREDENTIALS",
        "GOOGLE_CLOUD_PROJECT",
        "GOOGLE_CLOUD_QUOTA_PROJECT",
        "GOOGLE_CLOUD_LOCATION",
    )

    @property
    def name(self) -> str:
        return "antigravity"

    def cli_candidates(self) -> tuple[Path, ...]:
        # Where agy's own installer puts it, which a non-login shell's PATH
        # may not include yet.
        if sys.platform == "win32":
            local = os.environ.get("LOCALAPPDATA")
            return (Path(local) / "agy" / "bin" / "agy.exe",) if local else ()
        return (Path.home() / ".local" / "bin" / "agy",)

    def seed_files(self, ctx: RunContext) -> list[Path]:
        # settings.json picks the model provider (an API key login, custom
        # models), so it is part of the login and copied verbatim whatever
        # the user layer, as pi's is (docs/adr/0012). An ADC login
        # (`AGY_ADC_AUTH`) reads gcloud's credentials from APPDATA on Windows
        # and ~/.config/gcloud elsewhere.
        adc = (
            _windows_roaming(ctx.isolated_home)
            if sys.platform == "win32"
            else Path(ctx.isolated_home) / ".config"
        )
        return [
            self.attempt_config_home(ctx) / "antigravity-cli" / "settings.json",
            adc / "gcloud" / "application_default_credentials.json",
        ]

    def user_path(self, isolated: Path, ctx: RunContext) -> Path:
        if sys.platform == "win32" and (
            appdata := os.environ.get("APPDATA", "").strip()
        ):
            try:
                return Path(appdata) / isolated.relative_to(
                    _windows_roaming(ctx.isolated_home)
                )
            except ValueError:
                pass
        return super().user_path(isolated, ctx)

    def skills_root(self, ctx: RunContext) -> Path:
        # The CLI's own global skills dir, which the Antigravity app does not
        # read: an attempt meets only the CLI's neighbourhood.
        return self.attempt_config_home(ctx) / "antigravity-cli" / "skills"

    def user_skill_sources(self, ctx: RunContext) -> list[Path]:
        # agy also reads the global dirs it shares with the app and gemini-cli,
        # preferring config/skills over skills.
        home = self.user_config_home()
        return [
            *super().user_skill_sources(ctx),
            home / "config" / "skills",
            home / "skills",
        ]

    def _prepare(self, ctx: RunContext) -> None:
        # macOS finds the login keychain under ``$HOME/Library/Keychains``, so
        # with the isolated home agy finds no login and starts an OAuth flow.
        # Link the user's keychains in, as the operator's own agy reads them
        # (docs/adr/0038); that the agent can then reach them is
        # docs/adr/0027.
        if sys.platform != "darwin":
            return
        keychains = Path.home() / "Library" / "Keychains"
        link = Path(ctx.isolated_home) / "Library" / "Keychains"
        if keychains.is_dir() and not link.is_symlink():
            link.parent.mkdir(parents=True, exist_ok=True)
            link.symlink_to(keychains, target_is_directory=True)

    def _command(
        self, ctx: RunContext, planted: PlantedSession | None
    ) -> tuple[list[str], str | None, Callable[[], None] | None]:
        agy = self.cli_path() or "agy"
        cmd = [
            agy,
            *_print_flags(ctx.model, self._effort),
            # Headless mode cannot ask, and soft-denies anything that needs
            # approval: a shell command would be skipped, not run.
            "--dangerously-skip-permissions",
        ]
        return cmd, _user_message(ctx.prompt), None

    def _environment(self, ctx: RunContext) -> dict[str, str]:
        extra = dict(_NO_AUTOUPDATE)
        if sys.platform == "win32":
            # Go's os.UserConfigDir and os.UserCacheDir read APPDATA and
            # LOCALAPPDATA on Windows; USERPROFILE comes from ``_isolated_env``.
            roaming = _windows_roaming(ctx.isolated_home)
            extra["APPDATA"] = str(roaming)
            extra["LOCALAPPDATA"] = str(roaming.parent / "Local")
        return self._isolated_env(ctx, extra=extra)

    def _read(self, proc: ProcessResult, ctx: RunContext) -> AgentReport:
        report = _read_stream(proc.stdout)
        # The MCP inventory is the mcp_config.json the attempt ran with.
        report.read_mcp_servers = partial(
            AGY_MCP_CONFIG.servers, self.attempt_config_home(ctx)
        )
        return report

    # --- bare prompt call (the judge's half of the seam) -------------------

    def _prompt_command(self, prompt: str, model: str | None) -> PromptCall:
        agy = self.cli_path()
        if not agy:
            raise HarnessConfigurationError("agy CLI not found")
        return PromptCall(
            [agy, *_print_flags(model, self._effort)], stdin=_user_message(prompt)
        )

    def _prompt_environment(self) -> dict[str, str]:
        return {**super()._prompt_environment(), **_NO_AUTOUPDATE}

    def _read_prompt(self, proc: ProcessResult) -> AgentReport:
        return _read_stream(proc.stdout)

    def login_command(self) -> list[str]:
        return [self.cli_path() or "agy"]

    config_signals = (
        ConfigSignal(
            ("gemini_api_key environment variable is not set",),
            "Antigravity CLI is configured for a Gemini API key, but "
            "GEMINI_API_KEY is not set.\n\n"
            "Export `GEMINI_API_KEY` with your Gemini API key, or remove "
            '`"modelProvider": "gemini"` from '
            "`~/.gemini/antigravity-cli/settings.json` to use the account "
            "login, then rerun caliper. The agy CLI returned:\n"
            "  {text}",
        ),
        ConfigSignal(
            (
                *AUTH_MARKERS,
                "authentication required",
                "not logged into antigravity",
                "no stored credentials",
            ),
            "Antigravity CLI cannot run with the current authentication.\n\n"
            "Caliper drives the local agy CLI and reuses its sign-in (your OS "
            "keyring, or `GEMINI_API_KEY` / ADC from the environment). The agy "
            "CLI returned:\n"
            "  {text}\n\n"
            "Run `{login}` once to sign in, verify `agy -p 'Reply OK'` works in "
            "your normal shell, then rerun caliper.",
            login=True,
        ),
    )

    def _diagnose(
        self, proc: ProcessResult, report: AgentReport, cli_text: str
    ) -> str | None:
        """Catch a model agy does not know, so the run stops on it.

        agy exits 1 before reaching the model, and lists every model it knows
        after the one line that says so.
        """
        if proc.returncode == 0:
            return None
        said = next(
            (
                line.strip()
                for line in cli_text.splitlines()
                if "invalid model selection" in line
            ),
            None,
        )
        if said is None:
            return None
        return (
            "Antigravity CLI cannot run the requested model.\n\n"
            "Caliper ran {cli}, which returned:\n"
            f"  {said}\n\n"
            "Run `agy models` for the models agy can run, pass one as "
            "`--model antigravity:<model>`, then retry the eval."
        )


def _print_flags(model: str | None, effort: str | None) -> list[str]:
    """Print mode reading one stream-json message from stdin, and streaming back.

    No ``--print``: it takes the prompt as its value, and stream-json input
    is print mode already.
    """
    flags = ["--input-format", "stream-json"]
    flags += ["--output-format", "stream-json"]
    if model:
        flags += ["--model", model]
    if effort:
        flags += ["--effort", effort]
    return flags


def _user_message(prompt: str) -> str:
    """The prompt as agy's stream-json input: one ``user`` event, then EOF."""
    message = {"event": "user", "message": {"content": prompt}}
    return json.dumps(message) + "\n"


def _read_stream(stdout: str) -> AgentReport:
    """One walk of agy's stream-json events.

    Each finished step is a turn: a ``tool`` step is the call and its result,
    an ``agent_response`` step the text its ``ACTIVE`` updates streamed. Every
    finished step reports its own usage, summed into the run total, so a run
    cut off before its ``result`` event still has one. The ``result`` event
    names the answer, or the error agy stopped on.
    """
    report = AgentReport()
    usages: list[dict] = []
    deltas: dict[int, list[str]] = {}

    for event in stream_events(stdout):
        kind = event.get("event")

        if kind == "step_update":
            step = event.get("step_update")
            if not isinstance(step, dict):
                continue
            index = step.get("step_index")
            delta = step.get("text_delta")
            if isinstance(delta, str):
                deltas.setdefault(index, []).append(delta)
            if step.get("state") != "DONE":
                continue
            if isinstance(step.get("usage"), dict):
                usages.append(step["usage"])
            step_type = step.get("step_type")
            if step_type == "tool":
                report.transcript.extend(_tool_turns(step))
            elif step_type == "agent_response":
                text = "".join(deltas.pop(index, [])).strip()
                if text:
                    report.transcript.append(
                        ConversationTurn(role="assistant", content=text)
                    )
            continue

        if kind == "result":
            result = event.get("result")
            if not isinstance(result, dict):
                continue
            response = result.get("response")
            if isinstance(response, str) and response.strip():
                report.final_output = response.strip()
            error = result.get("error")
            if result.get("status") != "SUCCESS" and isinstance(error, str):
                report.cli_errors.append(error.strip())

    if usages:
        report.read_usage = partial(_usage, usages)
    return report


def _tool_turns(step: dict) -> list[ConversationTurn]:
    """A finished tool step as the call and what came back.

    agy reaches an MCP tool through one generic ``call_mcp_tool``; that call
    is named ``mcp_<server>_<tool>``, the name agy itself gives an eager MCP
    tool, so an MCP call reads the same whichever way agy loaded the tool
    (docs/adr/0043).
    """
    info = step.get("tool_info")
    info = info if isinstance(info, dict) else {}
    name = info.get("name") or step.get("tool_name")
    parameters = info.get("parameters")
    if name == "call_mcp_tool" and isinstance(parameters, dict):
        server, tool = parameters.get("ServerName"), parameters.get("ToolName")
        if isinstance(server, str) and isinstance(tool, str) and server and tool:
            name = mcp_tool_name("antigravity", server, tool)
            arguments = parameters.get("Arguments")
            parameters = arguments if isinstance(arguments, dict) else parameters
    output = info.get("output")
    error = info.get("error")
    if isinstance(error, dict) and isinstance(error.get("message"), str):
        output = error["message"]
    output = output if isinstance(output, str) else ""
    return [
        ConversationTurn(
            role="tool_use",
            content=f"[tool: {name}]",
            tool_name=name,
            tool_input=parameters if isinstance(parameters, dict) else None,
        ),
        ConversationTurn(role="tool_result", content=output, tool_output=output),
    ]


def _usage(usages: list[dict]) -> TokenUsage:
    """Sum the finished steps' usage into the run total.

    agy's ``input_tokens`` is already non-cached, beside ``cache_read_tokens``,
    and ``output_tokens`` already counts its ``thinking_tokens``.
    """
    fields = ("input_tokens", "output_tokens", "cache_read_tokens")
    return TokenUsage(**{field: _sum(usages, field) for field in fields})


def _sum(usages: list[dict], key: str) -> int:
    return sum(u[key] for u in usages if isinstance(u.get(key), int))
