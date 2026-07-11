from __future__ import annotations

import os
import subprocess
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Callable

from caliper.schema.results import TokenUsage
from caliper.schema.spec import McpServer


class HarnessConfigurationError(RuntimeError):
    """Raised when a harness cannot run because local configuration is invalid."""


@dataclass
class ConversationTurn:
    role: str
    content: str
    tool_name: str | None = None
    tool_input: dict | None = None
    tool_output: str | None = None


@dataclass
class AttemptResult:
    task_id: str
    attempt: int
    transcript: list[ConversationTurn]
    final_output: str
    exit_code: int
    duration_seconds: float
    error: str | None = None
    timed_out: bool = False
    cheated: bool = False
    cheat_evidence: list[str] = field(default_factory=list)
    # The concrete model the agent actually resolved for this attempt, when the
    # backend can report it (e.g. hermes echoes it in its session export). Lets a
    # run record the real model even when none was passed and the CLI's own
    # default was used. ``None`` when the backend cannot report it.
    resolved_model: str | None = None
    # Token accounting for this attempt, when the backend can extract it from its
    # own output. ``None`` when the backend cannot report it (see ``_usage``).
    usage: TokenUsage | None = None


@dataclass
class RunContext:
    """Everything one attempt needs, plus a scratch dict for backend hooks.

    Created fresh per ``run`` call and threaded through the hooks, so a backend
    can stash per-attempt state (credentials seen, per-attempt config dir) in
    ``extras`` without touching instance state — the harness object is shared
    across the runner's worker threads.
    """

    task_id: str
    attempt: int
    prompt: str
    skill_path: str | None
    model: str | None
    timeout: int
    isolated_home: str
    extra_path: list[str]
    # Declared MCP servers (name -> McpServer) the agent-under-test may use. The
    # literal ``${VAR}`` in each server's ``env`` is kept as authored; a backend
    # that supports MCP interpolates and materializes it at run time. ``None``
    # when the spec declares no ``mcp:`` block.
    mcp_servers: dict[str, McpServer] | None = None
    extras: dict = field(default_factory=dict)


@dataclass
class ProcessResult:
    """The normalized outcome of spawning a CLI agent once."""

    stdout: str
    stderr: str
    returncode: int
    timed_out: bool

    @property
    def error(self) -> str | None:
        """The default ``AttemptResult.error``: a timeout marker, else stderr."""
        if self.timed_out:
            return "timeout"
        return self.stderr or None


class HarnessBackend(ABC):
    """The narrow seam the runner depends on: run one attempt, report a name.

    Deliberately small — a test double or a future non-CLI backend only has to
    satisfy these two members. The shared CLI-agent lifecycle lives in
    :class:`CliHarness`, not here.
    """

    @property
    @abstractmethod
    def name(self) -> str: ...

    # Whether this backend can materialize declared ``mcp:`` servers for the
    # agent-under-test. Default ``False``: the run seam refuses to run a spec
    # that declares ``mcp:`` on a backend that cannot honor it (rather than
    # silently dropping the tools). A backend flips this to ``True`` when it
    # wires MCP support.
    supports_mcp: bool = False

    # Optional backend-specific guidance appended to the run seam's refusal when
    # this backend cannot honor ``mcp:``. Left ``None`` by a backend whose lack
    # of support is merely a not-yet-implemented slice (it gets the generic "not
    # supported yet" message). A backend whose agent will *never* support MCP
    # natively (a permanent, by-design stance) sets this to say so and point the
    # spec author at an alternative, so the refusal reads as permanent rather
    # than pending.
    mcp_unsupported_hint: str | None = None

    @abstractmethod
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
        mcp_servers: dict[str, McpServer] | None = None,
    ) -> AttemptResult: ...


class CliHarness(HarnessBackend):
    """Deep base owning the CLI-agent run lifecycle; backends fill in what varies.

    ``run`` is a template method: it prepares the isolated home, builds the
    command and environment, spawns the CLI agent once (timing it and handling
    timeouts uniformly), raises on a diagnosed misconfiguration, parses the
    stream, and assembles the ``AttemptResult``. A backend only implements the
    parts that genuinely differ between CLI agents — the command, the
    environment, and how to read that agent's stream.
    """

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
        mcp_servers: dict[str, McpServer] | None = None,
    ) -> AttemptResult:
        ctx = RunContext(
            task_id=task_id,
            attempt=attempt,
            prompt=prompt,
            skill_path=skill_path,
            model=model or self._model,
            timeout=timeout,
            isolated_home=isolated_home,
            extra_path=list(extra_path or []),
            mcp_servers=mcp_servers,
        )

        self._ensure_ready(ctx)
        self._prepare(ctx)
        cmd, stdin, cleanup = self._command(ctx)
        env = self._environment(ctx)

        start = time.monotonic()
        try:
            proc = self._execute(
                cmd, env=env, cwd=ctx.isolated_home, timeout=ctx.timeout, stdin=stdin
            )
        finally:
            if cleanup is not None:
                cleanup()
        duration = time.monotonic() - start

        transcript, final_output = self._parse_stream(proc.stdout)

        diagnostic = self._diagnose(proc, final_output)
        if diagnostic:
            raise HarnessConfigurationError(diagnostic)

        transcript, final_output = self._fallback(transcript, final_output, proc)

        return AttemptResult(
            task_id=ctx.task_id,
            attempt=ctx.attempt,
            transcript=transcript,
            final_output=final_output,
            exit_code=proc.returncode,
            duration_seconds=duration,
            error=self._error_field(proc, final_output),
            timed_out=proc.timed_out,
            resolved_model=self._resolved_model(proc, ctx),
            usage=self._safe_usage(proc, ctx),
        )

    # --- hooks a backend implements ---------------------------------------

    _model: str | None = None

    def _ensure_ready(self, ctx: RunContext) -> None:
        """Raise ``HarnessConfigurationError`` if the CLI can't run. Default: skip."""

    def _prepare(self, ctx: RunContext) -> None:
        """Seed the isolated home with auth/config before the agent runs."""

    @abstractmethod
    def _command(
        self, ctx: RunContext
    ) -> tuple[list[str], str | None, Callable[[], None] | None]:
        """Return ``(argv, stdin_payload, cleanup)`` for the agent invocation.

        ``stdin_payload`` is fed to the process's stdin when not ``None``;
        ``cleanup`` runs after the process exits (e.g. to remove a staged file).
        """

    @abstractmethod
    def _environment(self, ctx: RunContext) -> dict[str, str]: ...

    @abstractmethod
    def _parse_stream(self, stdout: str) -> tuple[list[ConversationTurn], str]: ...

    def _diagnose(self, proc: ProcessResult, final_output: str) -> str | None:
        """Return a human-readable misconfiguration message, or ``None``."""
        return None

    def _fallback(
        self,
        transcript: list[ConversationTurn],
        final_output: str,
        proc: ProcessResult,
    ) -> tuple[list[ConversationTurn], str]:
        """Salvage raw stdout as a single turn when nothing parsed out of it."""
        if not transcript and proc.stdout:
            return [
                ConversationTurn(role="assistant", content=proc.stdout)
            ], proc.stdout
        return transcript, final_output

    def _error_field(self, proc: ProcessResult, final_output: str) -> str | None:
        return proc.error

    def _resolved_model(self, proc: ProcessResult, ctx: RunContext) -> str | None:
        """The concrete model the agent used, if the backend can report it.

        Default: the model we requested (``None`` when we let the CLI pick its
        own default and cannot observe what it chose). A backend that surfaces
        the resolved model in its output overrides this.
        """
        return ctx.model

    def _usage(self, proc: ProcessResult, ctx: RunContext) -> TokenUsage | None:
        """The token accounting for this attempt, if the backend can report it.

        Default: ``None`` (usage unavailable). A backend that emits token counts
        in its stream/output overrides this to parse ``proc.stdout`` into a
        normalized :class:`TokenUsage` (``input_tokens`` non-cached; see its
        docstring for the disjoint-fields contract).
        """
        return None

    def _safe_usage(self, proc: ProcessResult, ctx: RunContext) -> TokenUsage | None:
        """Extract usage, but never let a token-accounting failure sink an attempt.

        Usage is optional (``None`` = unavailable, renders as "—"), so a malformed
        or schema-changed usage payload must degrade to ``None`` rather than raise
        and crash the whole eval. This is the single chokepoint every backend's
        ``_usage`` passes through.
        """
        try:
            return self._usage(proc, ctx)
        except Exception:
            return None

    # --- shared machinery -------------------------------------------------

    def _execute(
        self,
        cmd: list[str],
        *,
        env: dict[str, str],
        cwd: str,
        timeout: int,
        stdin: str | None,
    ) -> ProcessResult:
        """Spawn the agent once, timing out into a 124/timeout ProcessResult."""
        try:
            proc = subprocess.run(
                cmd,
                input=stdin,
                stdin=subprocess.DEVNULL if stdin is None else None,
                capture_output=True,
                encoding="utf-8",
                text=True,
                timeout=timeout,
                env=env,
                cwd=cwd,
            )
        except subprocess.TimeoutExpired:
            return ProcessResult("", "timeout", 124, True)
        except OSError as exc:
            return ProcessResult("", f"{self.name} CLI failed: {exc}", 1, False)
        return ProcessResult(
            stdout=proc.stdout.strip(),
            stderr=(proc.stderr or "").strip(),
            returncode=proc.returncode,
            timed_out=False,
        )

    def _version_ok(
        self, cli: str, *, timeout: int, args: tuple[str, ...] = ("--version",)
    ) -> bool:
        """True when ``cli --version`` exits 0 — the CLI is installed and runnable."""
        try:
            result = subprocess.run(
                [cli, *args], capture_output=True, text=True, timeout=timeout
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        return result.returncode == 0

    def _capture_output(self, cmd: list[str], *, timeout: int) -> str | None:
        """Run a helper command, returning its stripped stdout on a clean exit."""
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        out = result.stdout.strip()
        return out if result.returncode == 0 and out else None

    @staticmethod
    def _passthrough(env: dict[str, str], keys: tuple[str, ...]) -> dict[str, str]:
        """Copy the named vars from the parent environment when present."""
        for key in keys:
            if key in os.environ:
                env[key] = os.environ[key]
        return env
