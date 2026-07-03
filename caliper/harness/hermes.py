from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path
from typing import Callable

from caliper.harness.base import (
    ConversationTurn,
    CliHarness,
    HarnessConfigurationError,
    ProcessResult,
    RunContext,
)

# Config files copied verbatim into the isolated HERMES_HOME so the agent can
# authenticate. Deliberately excludes SOUL.md (persona) and MEMORY.md/USER.md:
# Hermes is a *stateful* agent, and admitting it as an apples-to-apples flat
# backend means stripping it to a neutral agent (see
# docs/adr/0005-hermes-backend-normalized-to-neutral-agent.md).
_SEED_FILES = ("auth.json", "config.yaml", ".env")


class HermesHarness(CliHarness):
    """CLI-subprocess backend for Nous Research's `hermes` coding agent.

    Hermes' non-interactive `-z/--oneshot` mode prints only the final response
    text, so the full trajectory (tool calls + results, needed by `expect:`
    autoraters) is recovered in a second step: after the run we `hermes sessions
    export` the single session persisted in the isolated home, whose JSONL is a
    standard OpenAI-style transcript. Both steps run in one shell invocation so
    the template's single timeout covers the expensive oneshot.

    Hermes is normalized to a neutral agent on every attempt: an isolated
    ``HERMES_HOME`` seeded with auth/config only (no persona/memory) and
    ``--ignore-rules`` on the command, so the only skill in play is the
    skill-under-test staged into ``HERMES_HOME/skills/<name>``.
    """

    def __init__(self, model: str | None = None) -> None:
        self._model = model

    @property
    def name(self) -> str:
        return "hermes"

    def _ensure_ready(self, ctx: RunContext) -> None:
        if not self._cli_available():
            raise HarnessConfigurationError(
                "hermes CLI is not available for the `hermes` backend.\n\n"
                "Install the Hermes Agent (`curl -fsSL "
                "https://hermes-agent.nousresearch.com/install.sh | bash`) and "
                "authenticate it (`hermes login`), or set `HERMES_CLI_PATH` to "
                "the hermes binary, then rerun caliper."
            )

    def _prepare(self, ctx: RunContext) -> None:
        # Isolate Hermes' whole home per attempt (parallel-safe; never mutates
        # the user's real ~/.hermes). Seeding only auth/config — not SOUL.md or
        # MEMORY.md — is what neutralizes the agent; --ignore-rules on the
        # command completes the strip.
        hermes_home = Path(ctx.isolated_home) / ".hermes"
        real_home = Path.home() / ".hermes"
        hermes_home.mkdir(parents=True, exist_ok=True)
        for filename in _SEED_FILES:
            src = real_home / filename
            if src.exists():
                shutil.copy2(src, hermes_home / filename)
        ctx.extras["hermes_home"] = str(hermes_home)

        skill_name = self._stage_skill(ctx, hermes_home)
        if skill_name:
            ctx.extras["skill_name"] = skill_name

    def _stage_skill(self, ctx: RunContext, hermes_home: Path) -> str | None:
        """Copy the already-staged skill into ``HERMES_HOME/skills/<name>``.

        The runner has already staged the skill directory (cheat surfaces
        excluded) into ``isolated_home``; we re-stage that sanitized copy as a
        local Hermes skill so ``--skills <name>`` loads it natively — preserving
        progressive disclosure (Hermes reads the body on demand via its
        ``skill_view`` tool). Copying from the staged copy, not the original,
        inherits the runner's forbidden-file exclusions.
        """
        if not ctx.skill_path:
            return None
        staged = Path(ctx.isolated_home) / "SKILL.md"
        if not staged.exists():
            return None

        name = self._skill_name(staged.read_text()) or "skill-under-test"
        dst = hermes_home / "skills" / name
        dst.mkdir(parents=True, exist_ok=True)
        home = Path(ctx.isolated_home)
        for item in sorted(home.rglob("*")):
            if not item.is_file():
                continue
            rel = item.relative_to(home)
            # Skip the Hermes home we are seeding inside the same dir.
            if rel.parts and rel.parts[0] == ".hermes":
                continue
            target = dst / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, target)
        return name

    def _skill_name(self, skill_text: str) -> str | None:
        match = re.search(r"^---\n(.*?)\n---", skill_text, flags=re.DOTALL)
        if not match:
            return None
        name = re.search(r"^name:\s*(.+?)\s*$", match.group(1), flags=re.MULTILINE)
        return name.group(1).strip() if name else None

    def _command(
        self, ctx: RunContext
    ) -> tuple[list[str], str | None, Callable[[], None] | None]:
        # One shell invocation: run oneshot (its final text and any error go to
        # stderr, where _diagnose reads them), then export the single persisted
        # session as JSONL on stdout for _parse_stream. `exit $rc` propagates the
        # oneshot's exit code so a failed run is classified as infra_error.
        skill = ctx.extras.get("skill_name")
        skills_arg = ' --skills "$CALIPER_SKILL"' if skill else ""
        script = (
            f'"$CALIPER_HERMES" -z "$CALIPER_PROMPT"{skills_arg} --ignore-rules 1>&2\n'
            "rc=$?\n"
            '"$CALIPER_HERMES" sessions export --source cli - 2>/dev/null\n'
            "exit $rc\n"
        )
        return ["/bin/sh", "-c", script], None, None

    def _environment(self, ctx: RunContext) -> dict[str, str]:
        path = os.environ.get("PATH", "")
        if ctx.extra_path:
            path = os.pathsep.join(ctx.extra_path) + os.pathsep + path

        env = {
            "HOME": ctx.isolated_home,
            "PATH": path,
            "HERMES_HOME": ctx.extras["hermes_home"],
            "CALIPER_HERMES": self._hermes_command() or "hermes",
            "CALIPER_PROMPT": ctx.prompt,
        }
        skill = ctx.extras.get("skill_name")
        if skill:
            env["CALIPER_SKILL"] = skill
        return self._passthrough(env, ("LANG", "LC_ALL", "TERM", "TMPDIR"))

    def _cli_available(self) -> bool:
        hermes = self._hermes_command()
        return hermes is not None and self._version_ok(
            hermes, timeout=15, args=("--version",)
        )

    def _hermes_command(self) -> str | None:
        configured = os.environ.get("HERMES_CLI_PATH")
        if configured and Path(configured).exists():
            return configured
        return shutil.which("hermes")

    def _parse_stream(self, stdout: str) -> tuple[list[ConversationTurn], str]:
        """Parse a `hermes sessions export` record into turns.

        The export is a single JSON object with a ``messages`` array of
        OpenAI-style messages: ``user``/``assistant``/``tool`` roles, with an
        assistant's ``tool_calls[].function`` carrying the call and a ``tool``
        message carrying the result (linked by ``tool_call_id``).
        """
        record = self._load_export(stdout)
        if record is None:
            return [], ""

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

        if not final_output:
            for turn in reversed(transcript):
                if turn.role == "assistant" and turn.content:
                    final_output = turn.content
                    break

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
        for line in stripped.splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict) and "messages" in obj:
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

    def _resolved_model(self, proc: ProcessResult, ctx: RunContext) -> str | None:
        # Hermes echoes the concrete model in its session export, so we record
        # what actually ran even when no model was passed and the config default
        # was used. Fall back to the requested model if the export lacks it.
        record = self._load_export(proc.stdout)
        if record:
            model = record.get("model")
            if isinstance(model, str) and model:
                return model
        return ctx.model

    def _error_field(self, proc: ProcessResult, final_output: str) -> str | None:
        # The oneshot's final text is routed to stderr, so stderr is only an
        # error signal when the run itself failed.
        if proc.timed_out:
            return "timeout"
        if proc.returncode != 0:
            return proc.stderr or None
        return None

    def _diagnose(self, proc: ProcessResult, final_output: str) -> str | None:
        if proc.returncode == 0:
            return None

        text = "\n".join(part for part in (proc.stdout, proc.stderr) if part).strip()
        if not text:
            return None
        lowered = text.lower()

        provider_markers = (
            "no api key",
            "missing api key",
            "no credentials",
            "auth is missing",
            "access_token",
            "out of extra usage",
            "insufficient credits",
            "no credits",
            "quota",
        )
        if any(marker in lowered for marker in provider_markers):
            return (
                "hermes cannot run with the current provider/credential "
                "configuration.\n\n"
                "Caliper copies your `~/.hermes` auth and config into an isolated "
                "home and runs `hermes -z`. The hermes CLI returned:\n"
                f"  {text[:500]}\n\n"
                "Make sure `~/.hermes/config.yaml`'s default model/provider points "
                "at a provider you have credits for (an earlier default may have "
                "gone stale), verify `hermes -z 'Reply OK'` works in your normal "
                "shell, then rerun caliper."
            )

        auth_markers = (
            "401",
            "unauthorized",
            "not logged in",
            "please login",
            "please run /login",
            "authentication",
            "invalid api key",
            "subscription",
        )
        if any(marker in lowered for marker in auth_markers):
            return (
                "hermes cannot run with the current authentication "
                "configuration.\n\n"
                "Caliper drives the local hermes CLI and reuses its `~/.hermes` "
                "credentials. The hermes CLI returned:\n"
                f"  {text[:500]}\n\n"
                "Authenticate hermes (`hermes login`), verify `hermes -z 'Reply "
                "OK'` works in your normal shell, then rerun caliper."
            )

        return None
