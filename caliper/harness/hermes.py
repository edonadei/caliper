from __future__ import annotations

import contextlib
import json
import os
import shutil
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import replace
from functools import partial
from pathlib import Path

from caliper.harness.base import (
    AgentReport,
    AttemptResult,
    CliHarness,
    ConversationTurn,
    HarnessConfigurationError,
    ProcessResult,
    PromptCall,
    RunContext,
    last_assistant,
    stream_events,
)
from caliper.harness.cli_config import YAML, CliConfig
from caliper.harness.refusal import AUTH_MARKERS, ConfigSignal
from caliper.schema.results import TokenUsage

# Config files copied verbatim into the isolated HERMES_HOME so the agent can
# authenticate. Deliberately excludes SOUL.md (persona) and MEMORY.md/USER.md:
# Hermes is a *stateful* agent, and admitting it as an apples-to-apples flat
# backend means stripping it to a neutral agent (see
# docs/adr/0005-hermes-backend-normalized-to-neutral-agent.md). config.yaml is
# rewritten rather than copied (``HERMES_CONFIG``).
_SEED_FILES = ("auth.json", ".env")


# Hermes reads MCP servers from config.yaml's ``mcp_servers``, beside its model
# settings. Isolated, the model endpoints and the execution host survive and the
# user's own servers do not: the tool-environment half of hermes'
# neutralization (docs/backends.md, docs/adr/0028). It takes the common server
# rendering unchanged and infers HTTP/SSE from the URL; remote OAuth needs an
# interactive browser flow the harness cannot drive, so only header-auth
# remotes are expressible.
def _pinned_config() -> dict:
    # A Windows attempt home is a named profile under the user's hermes root
    # (docs/adr/0037). Standalone, its own gateway serves only the attempt rather
    # than reserving every profile the user's running gateway already owns, and
    # that gateway in turn never adopts it.
    return {"gateway": {"standalone": True}} if sys.platform == "win32" else {}


HERMES_CONFIG = CliConfig(
    path=".hermes/config.yaml",
    codec=YAML,
    connection_keys=frozenset(
        {"model", "provider", "providers", "custom_providers", "terminal"}
    ),
    pinned=_pinned_config,
)


# Seconds for the export that recovers a timed-out attempt's session. A warm
# HERMES_HOME exports in about a second.
_RECOVERY_EXPORT_TIMEOUT = 5

# The assistant turn hermes v0.21 exports when it never reached the model.
_NOT_PROCESSED = "your request was not processed"

# A Windows attempt home is a named profile ``profiles/caliper-*`` under the
# user's hermes root, marked as caliper's so a sweep never touches a user
# profile; a leftover is swept once it is a day old (docs/adr/0037).
_WINDOWS_PROFILE_PREFIX = "caliper-"
_ATTEMPT_MARKER = ".caliper-attempt"
_STALE_ATTEMPT_SECONDS = 24 * 60 * 60
# Seconds for `hermes gateway stop` on the attempt's own gateway; it takes
# several, since the gateway drains before it exits.
_GATEWAY_STOP_TIMEOUT = 30


def user_hermes_home() -> Path:
    """The developer's own hermes home, as hermes itself places it by default.

    ``%LOCALAPPDATA%\\hermes`` on Windows (falling back under the user profile,
    as hermes does), ``~/.hermes`` elsewhere.
    """
    if sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA", "").strip()
        base = Path(local) if local else Path.home() / "AppData" / "Local"
        return base / "hermes"
    return Path.home() / ".hermes"


class HermesHarness(CliHarness):
    """CLI-subprocess backend for Nous Research's `hermes` coding agent.

    Hermes' non-interactive `-z/--oneshot` mode prints only the final response
    text, so the full trajectory (tool calls + results, needed by `expect:`
    autoraters) is recovered in a second step: after the run we `hermes sessions
    export` the single session persisted in the isolated home, whose JSONL is a
    standard OpenAI-style transcript. Both steps run in one shell invocation so
    the template's single timeout covers the expensive oneshot. If that shell
    times out, ``_recover_timed_out`` exports the partial session on its own.

    Hermes is normalized to a neutral agent on every attempt: an isolated
    ``HERMES_HOME`` seeded with auth/config only (no persona/memory) and
    ``--ignore-rules`` on the command, so the only skill in play is the
    skill-under-test staged into ``HERMES_HOME/skills/<name>``.

    On Windows that home cannot sit in the temp dir: hermes boots only from a
    ``HERMES_HOME`` under the root that owns its installed dependencies. So
    ``isolated_home/.hermes`` is a junction to a fresh directory under that root,
    released once the attempt is read (docs/adr/0037).
    """

    supports_mcp = True
    # Hermes keeps sessions in SQLite, and its only import path drops tool
    # results, so it cannot resume a planted history faithfully (docs/adr/0036).
    history_unsupported_hint = (
        "Hermes cannot resume planted turns faithfully: its session import "
        "drops tool calls and their output. Run tasks with history: on "
        "--model claude-code, --model codex or --model pi, or remove history: "
        "to run on hermes."
    )
    cli_config = HERMES_CONFIG
    user_settings_file = "config.yaml"
    cli_name = "hermes"
    cli_path_env_var = "HERMES_CLI_PATH"
    cli_unavailable_message = (
        "hermes CLI is not available for the `hermes` backend.\n\n"
        "Install the Hermes Agent (`curl -fsSL "
        "https://hermes-agent.nousresearch.com/install.sh | bash`) and "
        "authenticate it (`hermes auth add <provider>`, or `hermes model` to "
        "pick a provider and log in), or set `HERMES_CLI_PATH` to "
        "the hermes binary, then rerun caliper."
    )
    cli_version_timeout = 15
    # Hermes advertises installed skills to the model as name + truncated
    # description and exposes the model's choice as a named skill_view call —
    # the shape that makes a description measurable.
    activation_tool_names = frozenset({"skill_view"})

    def __init__(self, model: str | None = None) -> None:
        self._model = model

    @property
    def name(self) -> str:
        return "hermes"

    def run(self, ctx: RunContext) -> AttemptResult:
        attempt_home = self._attach_windows_home(ctx)
        try:
            return super().run(ctx)
        finally:
            # After the read, not in _command's cleanup: a timed-out attempt's
            # recovery export and the MCP inventory both read this home.
            if attempt_home is not None:
                self._release_windows_home(ctx, attempt_home)

    def seed_files(self, ctx: RunContext) -> list[tuple[Path, Path]]:
        # Isolate Hermes' whole home per attempt (parallel-safe; never mutates
        # the user's real hermes home). Seeding only auth/config — not SOUL.md or
        # MEMORY.md — is what neutralizes the agent; --ignore-rules on the
        # command completes the strip.
        real_home = user_hermes_home()
        hermes_home = self._hermes_home(ctx)
        return [(real_home / name, hermes_home / name) for name in _SEED_FILES]

    def user_path(self, isolated: Path, ctx: RunContext) -> Path:
        # The attempt's .hermes stands for the user's hermes home, which is not
        # under the real home on Windows.
        try:
            inside = isolated.relative_to(self._hermes_home(ctx))
        except ValueError:
            return super().user_path(isolated, ctx)
        return user_hermes_home() / inside

    @staticmethod
    def _hermes_home(ctx: RunContext) -> Path:
        """The per-attempt ``HERMES_HOME``, a function of the isolated home."""
        return Path(ctx.isolated_home) / ".hermes"

    def _attach_windows_home(self, ctx: RunContext) -> Path | None:
        """On Windows, point the attempt's ``.hermes`` at a fresh profile.

        A named profile under the root, so hermes resolves the root's committed
        dependency environment, and standalone from its first file on (the
        pinned config), so neither its gateway nor the user's reaches across.
        A junction rather than a symlink: creating one needs no privilege.
        ``None`` elsewhere, where the temp-dir home boots.
        """
        if sys.platform != "win32":
            return None
        # Imported here: the module exists only on Windows.
        import _winapi

        profiles = user_hermes_home() / "profiles"
        profiles.mkdir(parents=True, exist_ok=True)
        _sweep_stale_attempts(profiles)
        attempt_home = Path(
            tempfile.mkdtemp(prefix=_WINDOWS_PROFILE_PREFIX, dir=profiles)
        )
        link = self._hermes_home(ctx)
        try:
            (attempt_home / _ATTEMPT_MARKER).write_text("")
            (attempt_home / "config.yaml").write_text(YAML.dumps(_pinned_config()))
            link.parent.mkdir(parents=True, exist_ok=True)
            _winapi.CreateJunction(str(attempt_home), str(link))
        except BaseException:
            shutil.rmtree(attempt_home, ignore_errors=True)
            raise
        return attempt_home

    def _release_windows_home(self, ctx: RunContext, attempt_home: Path) -> None:
        # `hermes -z` starts an --idle-exit gateway for the attempt's profile,
        # which outlives the oneshot and holds files in the home: stop it first.
        cli = self.cli_path()
        if cli and (attempt_home / "gateway.lock").exists():
            # Best-effort: a gateway still running leaves a marked home behind.
            with contextlib.suppress(Exception):
                self._execute(
                    [cli, "gateway", "stop"],
                    env=self._environment(ctx),
                    cwd=ctx.workdir,
                    timeout=_GATEWAY_STOP_TIMEOUT,
                    stdin=None,
                )
        # Then the junction (removing it never touches its target), then the
        # home; whatever is still held is left, marked, for a later sweep.
        link = self._hermes_home(ctx)
        if os.path.lexists(link):
            os.rmdir(link)
        _remove_attempt_home(attempt_home)

    def _prepare(self, ctx: RunContext) -> None:
        self._hermes_home(ctx).mkdir(parents=True, exist_ok=True)

    def skills_root(self, ctx: RunContext) -> Path:
        return self._hermes_home(ctx) / "skills"

    def bundled_skill_names(self, source: Path) -> set[str]:
        # Hermes lists the skills it ships as `name:hash` lines.
        manifest = source / ".bundled_manifest"
        if not manifest.is_file():
            return set()
        return {
            line.split(":", 1)[0].strip()
            for line in manifest.read_text().splitlines()
            if line.strip()
        }

    def _command(
        self, ctx: RunContext
    ) -> tuple[list[str], str | None, Callable[[], None] | None]:
        # One shell invocation: run oneshot (its final text and any error go to
        # stderr, where _diagnose reads them), then export the single persisted
        # session as JSONL on stdout for _read — unfiltered, since v0.21
        # tags it `oneshot` where v0.18 said `cli`, and the isolated home holds
        # no other session. `exit $rc` propagates the
        # oneshot's exit code so a failed run is classified as infra_error.
        # No --skills: hermes' own help calls that flag "preload", and its system
        # prompt already advertises installed skills as name + description for
        # the model to choose from — exactly the condition activation needs.
        # --yolo bypasses interactive approval prompts (the hermes analog of
        # claude-code's --dangerously-skip-permissions and pi's --approve): the
        # neutral-agent harness runs non-interactively with stdin closed, so an
        # approval gate — e.g. before an MCP tool call — would hang until timeout.
        # The model travels through the environment, like the prompt, so no
        # caller-supplied string is ever spliced into the script.
        model = ' --model "$CALIPER_MODEL"' if ctx.model else ""
        script = (
            f'"$CALIPER_HERMES" -z "$CALIPER_PROMPT"{model} --yolo '
            "--ignore-rules 1>&2\n"
            "rc=$?\n"
            '"$CALIPER_HERMES" sessions export - 2>/dev/null\n'
            "exit $rc\n"
        )
        # Windows cannot spawn the POSIX path /bin/sh (WinError 2); its sh
        # (Git for Windows) is found on PATH.
        shell = "sh" if sys.platform == "win32" else "/bin/sh"
        return [shell, "-c", script], None, None

    def _environment(self, ctx: RunContext) -> dict[str, str]:
        hermes_home = self._hermes_home(ctx)
        if sys.platform == "win32":
            # The junction's target: hermes places a home under its root by the
            # resolved path, never the temp-dir link.
            hermes_home = Path(os.path.realpath(hermes_home))
        extra = {
            "HERMES_HOME": str(hermes_home),
            "CALIPER_HERMES": self.cli_path() or "hermes",
            "CALIPER_PROMPT": ctx.prompt,
            # A source-checkout hermes keeps its dependency state under
            # HERMES_HOME, so a fresh attempt home looks stale and hermes starts
            # rebuilding the shared install's venv before answering, which
            # outlasts the attempt timeout. The attempt must not repair the
            # install, only run it.
            "HERMES_DISABLE_LAZY_INSTALLS": "1",
        }
        if ctx.model:
            extra["CALIPER_MODEL"] = ctx.model
        if sys.platform == "win32":
            # Python on Windows ignores HOME: Path.home() reads USERPROFILE, so
            # the isolated home goes there too, or hermes "could not determine
            # home directory". Hermes finds its root through LOCALAPPDATA.
            extra["USERPROFILE"] = ctx.isolated_home
            local = os.environ.get("LOCALAPPDATA")
            if local:
                extra["LOCALAPPDATA"] = local
        return self._isolated_env(ctx, extra=extra)

    def _recover_timed_out(
        self, proc: ProcessResult, ctx: RunContext, env: dict[str, str]
    ) -> ProcessResult:
        # The timeout killed the shell before its own export ran, but hermes
        # persists the session as the turns happen: export what reached disk.
        # Best-effort, so a failed or unreadable export keeps the bare timeout.
        try:
            exported = self._execute(
                [env["CALIPER_HERMES"], "sessions", "export", "-"],
                env=env,
                cwd=ctx.workdir,
                timeout=_RECOVERY_EXPORT_TIMEOUT,
                stdin=None,
            )
            if exported.cancelled:
                # Interrupted mid-recovery: drop the attempt (docs/adr/0018).
                return replace(proc, cancelled=True)
            if exported.returncode != 0 or exported.timed_out:
                return proc
            record = self._load_export(exported.stdout)
            recovered = record is not None and bool(self._turns(record)[0])
        except Exception:
            return proc
        return replace(proc, stdout=exported.stdout) if recovered else proc

    # --- bare prompt call (the judge's half of the seam) -------------------

    def _prompt_command(self, prompt: str, model: str | None) -> PromptCall:
        hermes = self.cli_path()
        if not hermes:
            raise HarnessConfigurationError("hermes CLI not found")

        # A bare prompt is a single call: `hermes -z` prints only the final
        # response text. --ignore-rules keeps persona/memory out of the answer;
        # unlike an attempt run there is no isolated HERMES_HOME — the call
        # deliberately reuses the developer's real ~/.hermes.
        cmd = [hermes, "-z", prompt, "--ignore-rules"]
        if model:
            cmd[2:2] = ["--model", model]
        return PromptCall(cmd)

    def _read(self, proc: ProcessResult, ctx: RunContext) -> AgentReport:
        """Read the one `hermes sessions export` record on stdout.

        The record carries the turns, the session's token totals and the
        concrete model; the MCP inventory is the ``config.yaml`` the attempt
        ran with.
        """
        report = AgentReport(read_mcp_servers=partial(HERMES_CONFIG.servers, ctx))
        record = self._load_export(proc.stdout)
        if record is None:
            return report
        report.transcript, report.final_output = self._turns(record)
        report.read_usage = partial(_usage, record)
        model = record.get("model")
        if isinstance(model, str) and model:
            # Echoed even when no model was passed and the config default ran.
            report.resolved_model = model
        return report

    def _turns(self, record: dict) -> tuple[list[ConversationTurn], str]:
        """The export's ``messages`` as turns, plus the last assistant text.

        A ``messages`` array of OpenAI-style messages: ``user``/``assistant``/
        ``tool`` roles, with an assistant's ``tool_calls[].function`` carrying
        the call and a ``tool`` message carrying the result (linked by
        ``tool_call_id``).
        """
        transcript: list[ConversationTurn] = []
        final_output = ""
        for message in record.get("messages", []):
            if not isinstance(message, dict):
                continue
            role = message.get("role")
            content = message.get("content")
            text = content if isinstance(content, str) else ""

            if role == "assistant":
                if text.strip():
                    transcript.append(ConversationTurn(role="assistant", content=text))
                    final_output = text
                for call in message.get("tool_calls") or []:
                    turn = self._tool_use_turn(call)
                    if turn is not None:
                        transcript.append(turn)
            elif role == "tool":
                transcript.append(
                    ConversationTurn(
                        role="tool_result",
                        content=text,
                        tool_output=text,
                        tool_name=message.get("tool_name"),
                    )
                )
            elif role == "user":
                if text.strip():
                    transcript.append(ConversationTurn(role="user", content=text))

        return transcript, final_output

    def _load_export(self, stdout: str) -> dict | None:
        """Return the export record, tolerating extra non-JSON lines."""
        stripped = stdout.strip()
        if not stripped:
            return None
        try:
            obj = json.loads(stripped)
            return obj if isinstance(obj, dict) else None
        except json.JSONDecodeError:
            pass
        for obj in stream_events(stripped):
            if "messages" in obj:
                return obj
        return None

    def _tool_use_turn(self, call: object) -> ConversationTurn | None:
        if not isinstance(call, dict):
            return None
        function = call.get("function")
        if not isinstance(function, dict):
            return None
        tool_name = function.get("name") or "tool"
        tool_input = self._parse_arguments(function.get("arguments"))
        return ConversationTurn(
            role="tool_use",
            content=f"[tool: {tool_name}]",
            tool_name=tool_name,
            tool_input=tool_input,
        )

    def _parse_arguments(self, arguments: object) -> dict | None:
        if isinstance(arguments, dict):
            return arguments
        if isinstance(arguments, str) and arguments.strip():
            try:
                parsed = json.loads(arguments)
            except json.JSONDecodeError:
                return {"raw": arguments}
            return parsed if isinstance(parsed, dict) else {"raw": arguments}
        return None

    def _error_field(self, proc: ProcessResult, final_output: str) -> str | None:
        # The oneshot's final text is routed to stderr, so stderr is only an
        # error signal when the run itself failed.
        if proc.timed_out:
            return "timeout"
        if proc.returncode != 0:
            return proc.stderr or None
        return None

    config_signals = (
        # No model/provider is selected or its login has lapsed — distinct from a
        # bad/absent API key below: the remedy is to pick and authenticate a
        # model with `hermes model` (e.g. Nous Portal), not to export a key.
        # First, so the actionable `hermes model` guidance wins for these.
        ConfigSignal(
            (
                "no access token found",
                "nous portal login",
                "relogin",
                "no active model",
                "no model selected",
                "no default model",
                "no anthropic credentials found",
            ),
            "hermes has no usable model/provider configured for the eval.\n\n"
            "Caliper copies your `~/.hermes` auth and config into an isolated "
            "home and runs `hermes -z`. The hermes CLI returned:\n"
            "  {text}\n\n"
            "Select and authenticate a model provider with `{login}` "
            "(pick your model and complete its login), verify `hermes -z "
            "'Reply OK'` works in your normal shell, then rerun caliper. Pass "
            "`--model hermes:<model>` to override the default for one run.",
            login=True,
        ),
        # A bare "quota" is not here: an exhausted quota is a spending cap, and
        # stops the run with the cap's own message (docs/adr/0030).
        ConfigSignal(
            (
                "no api key",
                "missing api key",
                "no credentials",
                "auth is missing",
                "access_token",
                "out of extra usage",
                "insufficient credits",
                "no credits",
            ),
            "hermes cannot run with the current provider/credential "
            "configuration.\n\n"
            "Caliper copies your `~/.hermes` auth and config into an isolated "
            "home and runs `hermes -z`. The hermes CLI returned:\n"
            "  {text}\n\n"
            "Make sure `~/.hermes/config.yaml`'s default model/provider points "
            "at a provider you have credits for (an earlier default may have "
            "gone stale), verify `hermes -z 'Reply OK'` works in your normal "
            "shell, then rerun caliper.",
        ),
        ConfigSignal(
            AUTH_MARKERS,
            "hermes cannot run with the current authentication "
            "configuration.\n\n"
            "Caliper drives the local hermes CLI and reuses its `~/.hermes` "
            "credentials. The hermes CLI returned:\n"
            "  {text}\n\n"
            "Authenticate hermes (`hermes auth add <provider>`, or `{login}` "
            "to pick a provider and log in), verify `hermes -z 'Reply "
            "OK'` works in your normal shell, then rerun caliper.",
            login=True,
        ),
    )

    def login_command(self) -> list[str]:
        return [self.cli_path() or "hermes", "model"]

    def _cli_text(self, proc: ProcessResult, report: AgentReport) -> str:
        # The oneshot prints its reply on stderr beside any error, whatever the
        # exit code, so once the agent gave a real answer stderr is the agent's
        # too. v0.21's "not processed" turn is hermes talking, not an answer.
        answer = last_assistant(report.transcript)
        if answer and not answer.lower().startswith(_NOT_PROCESSED):
            return ""
        return super()._cli_text(proc, AgentReport())

    def _diagnose(
        self, proc: ProcessResult, report: AgentReport, cli_text: str
    ) -> str | None:
        return self._diagnose_unknown_model(cli_text)

    def _diagnose_unknown_model(self, cli_text: str) -> str | None:
        """Catch a model the provider rejected, so the run stops on it (#131).

        hermes v0.18 exits 0 on an unknown ``--model``, with the provider's 404
        on stderr and only the user turn in the export — left alone, every
        attempt would be graded on an empty answer. v0.21 exits 2 instead, which
        would still spend every attempt as an infra error. ``cli_text`` holds
        stderr only when hermes failed or the agent never spoke: its reply goes
        to stderr too, and may quote anything.
        """
        lowered = cli_text.lower()
        rejection_markers = (
            "404",
            "not found",
            "does not exist",
            "unknown model",
            "invalid model",
        )
        if "model" not in lowered or not any(
            marker in lowered for marker in rejection_markers
        ):
            return None
        return (
            "hermes could not run the requested model.\n\n"
            "Caliper ran {cli}, which returned:\n"
            f"  {cli_text.strip()[:500]}\n\n"
            "Check the model id in `--model hermes:<provider>/<model>` (verify "
            "`hermes -z 'Reply OK' --model <model>` works in your normal shell), "
            "then rerun caliper."
        )


def _sweep_stale_attempts(profiles: Path) -> None:
    """Remove Windows attempt homes a crashed or locked-out release left behind.

    Only caliper's own (the marker), and only those untouched for a day, so
    neither a user's profile nor a concurrent run's live home is at risk.
    """
    cutoff = time.time() - _STALE_ATTEMPT_SECONDS
    for leftover in profiles.glob(f"{_WINDOWS_PROFILE_PREFIX}*"):
        try:
            if (leftover / _ATTEMPT_MARKER).is_file() and (
                leftover.stat().st_mtime < cutoff
            ):
                _remove_attempt_home(leftover)
        except OSError:
            continue


def _remove_attempt_home(home: Path) -> None:
    """Remove what can be removed; keep the marker while anything else is held.

    A plain ``rmtree`` would delete the marker beside a locked file, leaving a
    leftover no later sweep could recognize as caliper's.
    """
    for entry in home.iterdir():
        if entry.name == _ATTEMPT_MARKER:
            continue
        if entry.is_dir() and not entry.is_symlink():
            shutil.rmtree(entry, ignore_errors=True)
        else:
            with contextlib.suppress(OSError):
                entry.unlink()
    if [entry.name for entry in home.iterdir()] == [_ATTEMPT_MARKER]:
        with contextlib.suppress(OSError):
            (home / _ATTEMPT_MARKER).unlink()
            home.rmdir()


def _usage(record: dict) -> TokenUsage | None:
    """The session export record's flat top-level token totals.

    Hermes aggregates usage per session, so the record carries session totals
    directly (no per-message summing). Its ``input_tokens`` is non-cached, so
    ``cache_read_tokens``/``cache_write_tokens`` map onto the disjoint cache
    fields.
    """
    fields = {
        "input_tokens": record.get("input_tokens"),
        "output_tokens": record.get("output_tokens"),
        "cache_read_tokens": record.get("cache_read_tokens"),
        "cache_creation_tokens": record.get("cache_write_tokens"),
    }
    if all(v is None for v in fields.values()):
        return None
    return TokenUsage(**fields)
