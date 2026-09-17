from __future__ import annotations

import os
import shutil
import subprocess
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable

from caliper import cancel
from caliper.harness.prompt_failure import (
    PromptFailure,
    PromptFailureKind,
)
from caliper.schema.results import TokenUsage
from caliper.schema.spec import McpServer
from caliper.skills import SkillRef, install_skills


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
    """What one invocation of the agent produced, and nothing about what it means.

    Deliberately carries no identity (the runner knows which attempt it asked
    for) and no verdict (cheat detection and grading happen on the other side
    of the seam, in :mod:`caliper.attempt`).
    """

    transcript: list[ConversationTurn]
    final_output: str
    exit_code: int
    duration_seconds: float
    error: str | None = None
    timed_out: bool = False
    # The concrete model the agent actually resolved for this attempt, when the
    # backend can report it (e.g. hermes echoes it in its session export). Lets a
    # run record the real model even when none was passed and the CLI's own
    # default was used. ``None`` when the backend cannot report it.
    resolved_model: str | None = None
    # Token accounting for this attempt, when the backend can extract it from its
    # own output. ``None`` when the backend cannot report it (see ``_usage``).
    usage: TokenUsage | None = None
    # True when a cancellation killed this invocation. Such an attempt is
    # discarded rather than recorded: it is the interrupt showing up in the
    # sample, not an observation about the skill (docs/adr/0018).
    cancelled: bool = False
    # True when nothing parsed out of the agent's stream and the raw stdout had
    # to be salvaged as a single turn. The agent did not converse: whatever is in
    # ``final_output`` is the CLI talking, not the agent answering — which is how
    # a provider signal is told apart from an agent *writing about* one
    # (docs/adr/0019).
    salvaged: bool = False


@dataclass
class RunContext:
    """Everything one attempt needs.

    Created fresh per ``run`` call and threaded through the hooks. It is the
    only per-attempt state a backend has: the harness object is shared across
    the runner's worker threads, so anything an attempt needs to remember must
    be derivable from the context (a config dir under ``isolated_home``, a file
    the template seeded there) rather than stashed on the instance.
    """

    task_id: str
    attempt: int
    prompt: str
    # The skill neighbourhood to install at this backend's skills root before
    # the agent runs, never preloaded into its context. Already reduced by any
    # ``--ablate``; empty for a bare-agent run.
    skill_refs: list[SkillRef]
    model: str | None
    timeout: int
    isolated_home: str
    extra_path: list[str]
    # ``sandbox.forbidden_files`` — applied to the install so a skill's answer
    # key never travels with it.
    forbidden_files: list[str] = field(default_factory=list)
    # Declared MCP servers (name -> McpServer) the agent-under-test may use,
    # minus anything ``--ablate`` removed. The literal ``${VAR}`` in each
    # server's ``env`` is kept as authored; a backend that supports MCP
    # interpolates and materializes it at run time. ``None`` means the spec had
    # no ``mcp:`` block; an empty mapping means it had one whose servers were all
    # ablated, which a backend still isolates to zero servers.
    mcp_servers: dict[str, McpServer] | None = None

    def __post_init__(self) -> None:
        """The context owns its lists, and tolerates ``None`` for the optional ones.

        The runner holds one neighbourhood for the whole run and hands it to
        every attempt, and the harness object is shared across worker threads —
        so a backend that appended to what it was given would be editing the
        next attempt's inputs, on another thread. Copied here rather than at
        each call site: the guarantee belongs to the value that crosses the
        seam, not to whoever built it.

        The three list fields only. ``mcp_servers`` is deliberately shared: it
        is read-only to every backend that has one.
        """
        self.skill_refs = list(self.skill_refs or [])
        self.extra_path = list(self.extra_path or [])
        self.forbidden_files = list(self.forbidden_files or [])


@dataclass
class ProcessResult:
    """The normalized outcome of spawning a CLI agent once."""

    stdout: str
    stderr: str
    returncode: int
    timed_out: bool
    # True when this process was killed by a cancellation rather than by its own
    # failure or the timeout. Carried so the runner can drop the attempt instead
    # of recording an interrupt as an infrastructure failure (docs/adr/0018).
    cancelled: bool = False

    @property
    def error(self) -> str | None:
        """The default ``AttemptResult.error``: a timeout marker, else stderr."""
        if self.timed_out:
            return "timeout"
        return self.stderr or None


@dataclass
class PromptResult:
    """The outcome of running one bare prompt through a CLI agent.

    The judge's half of the backend seam: ``text`` is the agent's final answer,
    ``resolved_model`` the concrete model when the backend can report it (else
    the requested one, ``None`` on an unobserved CLI default), and ``error`` a
    human-readable reason when no answer was produced at all. When the harness
    classifies an upstream API failure, ``failure`` carries the typed kind and
    ``error`` is the formatted judge-facing message.
    """

    text: str
    resolved_model: str | None = None
    error: str | None = None
    failure: PromptFailure | None = None


@dataclass
class PromptCall:
    """How to spawn one bare prompt, and how to read its answer back.

    ``read`` is how the backend turns the finished process into a
    :class:`PromptResult`; ``None`` means its ``_prompt_output``. A backend
    whose answer lives somewhere the process left behind (codex writes it to a
    file named on argv) closes over that location here, so no scratch state
    has to cross between the command hook and the output hook. ``cleanup``
    runs once the call is over — answered, timed out, or raised — so a staged
    file is removed however the process ended.
    """

    argv: list[str]
    stdin: str | None = None
    read: Callable[[ProcessResult], PromptResult] | None = None
    cleanup: Callable[[], None] | None = None


class HarnessBackend(ABC):
    """The narrow seam the runner and judge depend on.

    Two capabilities: run one eval attempt (``run``), and run one bare prompt
    for an autorater-style call (``run_prompt``). Deliberately small — a test
    double or a future non-CLI backend only has to satisfy these members. The
    shared CLI-agent lifecycle lives in :class:`CliHarness`, not here.
    """

    @property
    @abstractmethod
    def name(self) -> str: ...

    # The model this backend was built to run, or ``None`` for the CLI's own
    # default. With ``name``, the engine a run records in ``RunMeta``: the
    # runner asks the harness rather than being told separately (docs/adr/0004).
    _model: str | None = None

    @property
    def model(self) -> str | None:
        return self._model

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

    # Tool names that mean "the agent deliberately opened a skill" on this
    # backend — claude-code's ``Skill``, hermes' ``skill_view``. Backends whose
    # agents reach a skill with a plain file read (codex, pi) leave this empty
    # and are detected by the path shape instead; the detector takes the *union*
    # of both, so a dedicated-tool backend is still caught reading the file
    # directly. Facts about the backend, not an algorithm — the matching lives
    # once, in the runner (docs/adr/0014).
    activation_tool_names: frozenset[str] = frozenset()

    @abstractmethod
    def run(self, ctx: RunContext) -> AttemptResult:
        """Run one attempt and report what came back.

        The narrow seam (docs/adr/0003): a :class:`RunContext` in, an
        :class:`AttemptResult` out, and nothing about scoring, judging or
        threads crosses it in either direction. The caller builds a fresh
        context per invocation — a retried attempt is a second invocation, and
        must not start from what the failed one left behind (docs/adr/0019,
        docs/adr/0023).
        """
        ...

    def run_prompt(
        self,
        prompt: str,
        *,
        model: str | None = None,
        cwd: str,
        timeout: int = 60,
    ) -> PromptResult:
        """Run one bare prompt through the agent and return its final text.

        Default: unsupported. :class:`CliHarness` provides the real template;
        a non-CLI backend that cannot answer a bare prompt inherits this.
        """
        return PromptResult(
            text="",
            resolved_model=model,
            error=f"backend {self.name!r} cannot run a bare prompt",
        )


class CliHarness(HarnessBackend):
    """Deep base owning the CLI-agent run lifecycle; backends fill in what varies.

    ``run`` is a template method: it prepares the isolated home, builds the
    command and environment, spawns the CLI agent once (timing it and handling
    timeouts uniformly), raises on a diagnosed misconfiguration, parses the
    stream, and assembles the ``AttemptResult``. A backend only implements the
    parts that genuinely differ between CLI agents — the command, the
    environment, and how to read that agent's stream.

    Where a chore is the same for every CLI agent, the backend *declares* what
    varies and this class performs it: ``seed_files``, ``cli_name`` and friends,
    ``env_passthrough``. See
    docs/adr/0020-a-backend-declares-its-chores-rather-than-performing-them.md.
    """

    def run(self, ctx: RunContext) -> AttemptResult:
        # The one fact the backend contributes to its own context: a request
        # naming no model means "whatever engine this backend was built with"
        # (docs/adr/0004). Settled once, up front, so every hook below reads a
        # ``ctx.model`` that is already the model the agent will actually run.
        #
        # Onto a *copy*, because the caller owns what it handed across the seam
        # and a backend that edited it would be reaching back through — the same
        # rule ``RunContext.__post_init__`` enforces for the lists.
        ctx = replace(ctx, model=ctx.model or self._model)

        self._ensure_ready(ctx)
        self._seed_home(ctx)
        self._prepare(ctx)
        # After _prepare: a backend's skills root can depend on state _prepare
        # sets up (hermes' HERMES_HOME, pi's agent dir).
        self._install_skills(ctx)
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

        transcript, final_output = self._parse_stream_with_tail(proc.stdout)

        diagnostic = self._diagnose(proc, final_output)
        if diagnostic:
            raise HarnessConfigurationError(diagnostic)

        # Whether the agent actually conversed, captured before the salvage below
        # can paper over the difference.
        parsed = bool(transcript)
        transcript, final_output = self._fallback(transcript, final_output, proc)

        return AttemptResult(
            transcript=transcript,
            final_output=final_output,
            exit_code=proc.returncode,
            duration_seconds=duration,
            error=self._error_field(proc, final_output),
            timed_out=proc.timed_out,
            resolved_model=self._resolved_model(proc, ctx),
            usage=self._safe_usage(proc, ctx),
            cancelled=proc.cancelled,
            salvaged=not parsed,
        )

    def run_prompt(
        self,
        prompt: str,
        *,
        model: str | None = None,
        cwd: str,
        timeout: int = 60,
    ) -> PromptResult:
        """Run one bare prompt through the CLI agent; the judge's template method.

        Unlike ``run`` there is no isolated home, no skill staging, and no MCP:
        the call runs in the caller's real environment (a judge deliberately
        reuses the developer's own auth/config). A backend fills in the argv
        (``_prompt_command``) and how to read the answer out of its output
        (``_prompt_output``); spawning, timeout, and error normalization live
        here.
        """
        model = model or self._model
        try:
            call = self._prompt_command(prompt, model)
        except HarnessConfigurationError as exc:
            return PromptResult(text="", resolved_model=model, error=str(exc))

        try:
            proc = self._execute(
                call.argv,
                env=self._prompt_environment(),
                cwd=cwd,
                timeout=timeout,
                stdin=call.stdin,
            )
            if proc.timed_out:
                return PromptResult(
                    text="",
                    resolved_model=model,
                    error=f"{self.name} prompt call timed out after {timeout}s",
                )
            if call.read is not None:
                return call.read(proc)
            return self._prompt_output(proc, model)
        finally:
            if call.cleanup is not None:
                call.cleanup()

    # --- hooks a backend implements ---------------------------------------

    def _ensure_ready(self, ctx: RunContext) -> None:
        """Raise ``HarnessConfigurationError`` if the CLI can't run. Default: skip."""

    def seed_files(self, ctx: RunContext) -> list[tuple[Path, Path]]:
        """The ``(real, isolated)`` config files to copy verbatim into the home.

        Declared as *data* rather than copied by hand, because the policy is the
        same for every CLI agent (copy verbatim, skip what isn't there) while the
        file list is the only part that differs — including its deliberate
        omissions: hermes leaves out SOUL.md/MEMORY.md to normalize the agent
        (docs/adr/0005), and codex's ``config.toml`` is absent here because it is
        rewritten rather than copied. Default: nothing to seed.

        See docs/adr/0012-cli-harnesses-copy-cli-config-verbatim.md and
        docs/adr/0020-a-backend-declares-its-chores-rather-than-performing-them.md.
        """
        return []

    def _seed_home(self, ctx: RunContext) -> None:
        """Copy each declared seed file that exists, creating parents as needed."""
        for src, dst in self.seed_files(ctx):
            if src.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)

    def _prepare(self, ctx: RunContext) -> None:
        """Seed the isolated home with anything ``seed_files`` cannot express.

        Runs after :meth:`_seed_home`, so a backend that has to *rewrite* a
        config (codex's stripped ``config.toml``, hermes' normalized
        ``mcp_servers``) sees the verbatim copy already in place, and so
        claude-code can tell whether credentials were seeded before it falls
        back to the Keychain. That order is part of the contract (docs/adr/0020).
        """

    @abstractmethod
    def skills_root(self, ctx: RunContext) -> Path:
        """Where this agent discovers skills, inside the attempt's isolated home.

        The one backend-specific fact install-and-discover needs. Called after
        ``_prepare``, so it may read state that hook set up.
        """

    def _install_skills(self, ctx: RunContext) -> None:
        """Install the declared neighbourhood; never preload any of it.

        Nothing is placed in the agent's context — it meets each skill as a name
        and a ``description`` it may or may not reach for, which is what makes
        the choice measurable (docs/adr/0013).
        """
        if ctx.skill_refs:
            install_skills(ctx.skill_refs, self.skills_root(ctx), ctx.forbidden_files)

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
    def _parse_stream(self, stdout: str) -> tuple[list[ConversationTurn], str]:
        """Read this agent's stream into turns plus whatever it named as final.

        Return ``""`` for the final output when the stream carried no explicit
        final-answer event; :meth:`_parse_stream_with_tail` supplies the tail. A backend
        never walks the transcript backwards itself.
        """

    def _parse_stream_with_tail(
        self, stdout: str
    ) -> tuple[list[ConversationTurn], str]:
        """Parse the agent's stream, falling back to its last assistant turn.

        Every CLI agent has some shape of stream that may end without naming a
        final answer — a tool call last, a truncated run — and the answer in that
        case is the same for all of them: the last thing the assistant said. So
        the tail lives here rather than at the end of four ``_parse_stream``
        implementations. Distinct from :meth:`_fallback`, which salvages raw
        stdout when *nothing* parsed at all.
        """
        transcript, final_output = self._parse_stream(stdout)
        return transcript, final_output or self._last_assistant(transcript)

    @staticmethod
    def _last_assistant(transcript: list[ConversationTurn]) -> str:
        """The most recent non-empty assistant turn's content, or ``""``."""
        for turn in reversed(transcript):
            if turn.role == "assistant" and turn.content:
                return turn.content
        return ""

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

    def _prompt_command(self, prompt: str, model: str | None) -> PromptCall:
        """The invocation for a bare prompt call.

        Raise ``HarnessConfigurationError`` when the CLI is missing.
        """
        raise NotImplementedError(f"{self.name} does not implement _prompt_command")

    def _prompt_environment(self) -> dict[str, str]:
        """The env for a bare prompt call. Default: the caller's real environment."""
        return dict(os.environ)

    def _prompt_output(self, proc: ProcessResult, model: str | None) -> PromptResult:
        """Read the agent's final answer out of a finished prompt call.

        The default ``PromptCall.read``; a backend that needs more than the
        process itself supplies its own ``read`` instead.
        """
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout).strip()
            suffix = f": {detail[:200]}" if detail else ""
            failure = PromptFailure(
                kind=PromptFailureKind.OTHER,
                message=f"{self.name} judge exited {proc.returncode}{suffix}",
            )
            # The harness carries the structural failure; the judge formats it
            # (see caliper/judge/script_assert.py). ``error`` holds the raw
            # message for callers that only read text.
            return PromptResult(
                text="",
                resolved_model=model,
                error=failure.message,
                failure=failure,
            )
        return PromptResult(
            text=self._prompt_text(proc), resolved_model=model, error=None
        )

    def _prompt_text(self, proc: ProcessResult) -> str:
        """The final answer text on a clean exit. Default: raw stdout."""
        return proc.stdout.strip()

    # --- shared machinery -------------------------------------------------

    #: The binary :meth:`cli_path` looks for on ``PATH``. ``None`` for a backend
    #: that resolves its command some other way (claude-code spawns ``claude``
    #: through the shell's own lookup).
    cli_name: str | None = None

    #: The env var that overrides :attr:`cli_name` with an explicit path. Honored
    #: only when it points at something that exists, so a stale export falls
    #: through to discovery rather than failing the run with a confusing message.
    cli_path_env_var: str | None = None

    def cli_candidates(self) -> tuple[Path, ...]:
        """Well-known install locations to try before ``PATH``. Default: none.

        For an agent shipped inside an application bundle, which is where the
        current build lives even when an older copy is on ``PATH``. Resolved at
        call time, so a candidate may depend on state the process picks up.
        """
        return ()

    def cli_path(self) -> str | None:
        """Locate this backend's CLI: env-var override, then candidates, then PATH.

        One order for every backend. Without it each adapter spelled the same
        three steps itself, and they drifted — the override was checked for
        existence in some and not others.
        """
        if self.cli_path_env_var:
            configured = os.environ.get(self.cli_path_env_var)
            if configured and Path(configured).exists():
                return configured
        for candidate in self.cli_candidates():
            if candidate.exists():
                return str(candidate)
        return shutil.which(self.cli_name) if self.cli_name else None

    #: Vars forwarded from the parent environment into an isolated run. Locale
    #: and terminal shape are not state the agent carries between attempts, and
    #: an agent that cannot find its scratch space fails for reasons that have
    #: nothing to do with the skill under test.
    env_passthrough: tuple[str, ...] = ("LANG", "LC_ALL", "TERM", "TMPDIR")

    def _isolated_env(
        self,
        ctx: RunContext,
        *,
        extra: dict[str, str] | None = None,
        path_prefixes: list[str] | None = None,
    ) -> dict[str, str]:
        """Build the attempt's environment: a stripped ``HOME`` plus a usable ``PATH``.

        The ``HOME`` is the isolated one — that is the whole point of the
        per-attempt home — and everything else is opt-in, so an attempt cannot
        quietly inherit the developer's ambient state. ``ctx.extra_path`` comes
        first (the run's own staged binaries win), then any backend prefixes,
        then the real ``PATH`` with those entries removed so a prefix genuinely
        takes precedence instead of merely appearing twice.
        """
        prefixes = list(dict.fromkeys([*ctx.extra_path, *(path_prefixes or [])]))
        rest = [
            part
            for part in os.environ.get("PATH", "").split(os.pathsep)
            if part and part not in set(prefixes)
        ]
        env = {
            "HOME": ctx.isolated_home,
            "PATH": os.pathsep.join(prefixes + rest),
            **(extra or {}),
        }
        return self._passthrough(env, self.env_passthrough)

    def _execute(
        self,
        cmd: list[str],
        *,
        env: dict[str, str],
        cwd: str,
        timeout: int,
        stdin: str | None,
    ) -> ProcessResult:
        """Spawn the agent once, timing out into a 124/timeout ProcessResult.

        ``Popen`` rather than ``subprocess.run`` so the live process is
        registered with :mod:`caliper.cancel`: an interrupt has to be able to
        kill an agent mid-flight, or Ctrl-C waits out the full ``--timeout`` of
        every attempt already running. ``start_new_session`` gives each agent
        its own process group, which is what lets a timeout or a cancellation
        take the tools it spawned down with it instead of orphaning them.
        """
        try:
            with subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL if stdin is None else subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                encoding="utf-8",
                text=True,
                env=env,
                cwd=cwd,
                start_new_session=True,
            ) as proc:
                with cancel.track(proc):
                    try:
                        stdout, stderr = proc.communicate(input=stdin, timeout=timeout)
                    except subprocess.TimeoutExpired:
                        cancel.kill(proc)
                        proc.communicate()
                        return ProcessResult("", "timeout", 124, True)
        except OSError as exc:
            return ProcessResult("", f"{self.name} CLI failed: {exc}", 1, False)
        return ProcessResult(
            stdout=(stdout or "").strip(),
            stderr=(stderr or "").strip(),
            returncode=proc.returncode,
            timed_out=False,
            cancelled=cancel.was_killed(proc),
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
