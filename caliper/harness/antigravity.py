from __future__ import annotations

import json
import os
import sys
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
from caliper.harness.history import PlantedSession
from caliper.harness.refusal import AUTH_MARKERS, ConfigSignal
from caliper.schema.results import TokenUsage


class AntigravityHarness(CliHarness):
    """CLI-subprocess backend for Google's Antigravity CLI (`agy`).

    Drives `agy` in print mode with its stream-json output. The prompt goes in
    on stdin as one stream-json ``user`` message rather than as ``--print``'s
    value: agy refuses a flag-shaped ``--print`` value outright, and stdin
    takes any text as the task. Skills are installed where agy discovers them
    (``~/.gemini/antigravity-cli/skills``) and left to be found
    (docs/adr/0013).
    """

    def __init__(self, model: str | None = None) -> None:
        self._model = model

    # stdout is only the event stream: one that did not parse is no answer.
    salvages_raw_stdout = False

    cli_name = "agy"
    cli_path_env_var = "AGY_CLI_PATH"
    # agy reads no variable for its config home, so the attempt's isolated
    # ``HOME`` is what points it at the attempt's own.
    config_home = ".gemini"
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
        # (`AGY_ADC_AUTH`) reads gcloud's credentials from the home.
        return [
            self.attempt_config_home(ctx) / "antigravity-cli" / "settings.json",
            Path(ctx.isolated_home)
            / ".config"
            / "gcloud"
            / "application_default_credentials.json",
        ]

    def skills_root(self, ctx: RunContext) -> Path:
        # The CLI's own global skills dir, which the Antigravity app does not
        # read: an attempt meets only the CLI's neighbourhood.
        return self.attempt_config_home(ctx) / "antigravity-cli" / "skills"

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
            *_print_flags(ctx.model),
            # Headless mode cannot ask, and soft-denies anything that needs
            # approval: a shell command would be skipped, not run.
            "--dangerously-skip-permissions",
        ]
        return cmd, _user_message(ctx.prompt), None

    def _environment(self, ctx: RunContext) -> dict[str, str]:
        # A background self-update would rewrite the binary other attempts of
        # the run are spawning.
        return self._isolated_env(ctx, extra={"AGY_CLI_DISABLE_AUTO_UPDATE": "1"})

    def _read(self, proc: ProcessResult, ctx: RunContext) -> AgentReport:
        return _read_stream(proc.stdout)

    # --- bare prompt call (the judge's half of the seam) -------------------

    def _prompt_command(self, prompt: str, model: str | None) -> PromptCall:
        agy = self.cli_path()
        if not agy:
            raise HarnessConfigurationError("agy CLI not found")
        return PromptCall([agy, *_print_flags(model)], stdin=_user_message(prompt))

    def _read_prompt(self, proc: ProcessResult) -> AgentReport:
        return _read_stream(proc.stdout)

    def login_command(self) -> list[str]:
        return [self.cli_path() or "agy"]

    config_signals = (
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


def _print_flags(model: str | None) -> list[str]:
    """Print mode reading one stream-json message from stdin, and streaming back.

    No ``--print``: it takes the prompt as its value, and stream-json input
    is print mode already.
    """
    flags = ["--input-format", "stream-json"]
    flags += ["--output-format", "stream-json"]
    if model:
        flags += ["--model", model]
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
    """A finished tool step as the call and what came back."""
    info = step.get("tool_info")
    info = info if isinstance(info, dict) else {}
    name = info.get("name") or step.get("tool_name")
    parameters = info.get("parameters")
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
