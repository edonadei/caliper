from __future__ import annotations

import os
import select
import shutil
import subprocess
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from caliper import cancel
from caliper.activation import ActivationDetector
from caliper.attempt import assemble_attempt
from caliper.harness.base import (
    HarnessBackend,
    HarnessConfigurationError,
    RunContext,
)
from caliper.judge.base import Judge
from caliper.retry import SpendingCapReached, invoke_with_retry
from caliper.sandbox import SpecSandbox
from caliper.schema.results import (
    ERA_INSTALL_AND_DISCOVER,
    AggregateScore,
    AttemptRecord,
    HookFailure,
    HookPhase,
    Outcome,
    RunMeta,
    RunResults,
    TaskResult,
)
from caliper.schema.spec import EvalSpec, McpServer, TaskSpec, spec_name
from caliper.skillfetch import SkillFetcher
from caliper.skills import (
    SkillRef,
    apply_ablation,
    resolve_skills,
    validate_activates,
)
from caliper.skillsnapshot import snapshot_skill

_FAIL_FAST_OUTCOMES = {Outcome.INFRA_ERROR, Outcome.TIMEOUT}


class RunAborted(RuntimeError):
    """A fatal error stopped the run; the attempts already paid for ride along.

    Carries the partial :class:`RunResults` so the caller can save them *before*
    surfacing ``cause``. A credential that expires at attempt 30 should cost the
    remaining attempts, not the 29 that already ran.
    """

    def __init__(self, cause: Exception, results: RunResults) -> None:
        super().__init__(str(cause))
        self.cause = cause
        self.results = results


@dataclass
class AttemptEvent:
    task_id: str
    attempt: int
    outcome: Outcome


@dataclass(frozen=True)
class _RunEnv:
    """Everything constant across a run's tasks and attempts.

    Threaded as one value so the scheduling functions and ``_run_attempt``
    keep a readable signature — they vary only by task and attempt number.
    """

    harness: HarnessBackend
    judge: Judge
    sandbox: SpecSandbox
    activation: ActivationDetector
    spec: EvalSpec
    spec_path: Path
    # The skills actually installed: the declared neighbourhood minus anything
    # ``--ablate`` removed.
    skill_refs: list[SkillRef]
    # The mcp: servers left after ``--ablate``; the backend materializes these.
    mcp_servers: dict[str, McpServer]
    # Whether the spec declared an ``mcp:`` block at all — by field presence,
    # since ``mcp: {}`` is a declared block with no servers, not an absent one.
    # It tells the backend "no block" (``None``) from "every declared server
    # was ablated" (an empty mapping). Both isolate the attempt to zero servers
    # (docs/adr/0026-attempts-never-see-account-connectors.md).
    mcp_declared: bool
    # The *skills* ``--ablate`` removed. Truthy drops every task's activation
    # expectation. Removing a server is deliberately not on this list: activation
    # asserts on skills, and those are still installed and observable.
    ablated_skills: list[str]
    timeout: int
    fail_fast_unusable: int
    on_attempt_done: Callable[[AttemptEvent], None] | None
    on_task_done: Callable[[TaskResult], None] | None
    # Collect the concrete model each attempt/judge call resolved, so RunMeta can
    # record what really ran even on a CLI default. list.append is atomic under
    # the GIL, so these are safe to share across the pool's worker threads.
    resolved_models: list[str]
    judge_models: list[str]
    # The first fatal misconfiguration a worker diagnosed, if any. Collected
    # rather than raised through the pool so the run can be saved before it is
    # surfaced; same append-only, GIL-safe discipline as the two lists above.
    fatal: list[Exception]
    hook_failures: list[HookFailure]

    def expected_activation(self, task: TaskSpec) -> list[str] | None:
        """What this run asserts the task should activate — ``None`` if a skill was ablated.

        An ablated **skill** run **drops** the expectation rather than filtering
        the removed skill out of it. Filtering would assert a claim the author
        never wrote, and it inverts the delegating case: remove a parent and its
        neighbours correctly stop firing, so scoring that as a miss would report
        the finding as a failure. The observation is still recorded; only the
        verdict is withheld, so the column renders skipped rather than 0%. See
        docs/adr/0015-ablation-names-its-subject-at-the-invocation.md.

        Ablating a *server* leaves the expectation alone: ``activates:`` names
        skills, and every one of them is still installed, so withholding their
        verdict would drop a measurement nothing removed. See
        docs/adr/0025-ablation-covers-mcp-servers.md.

        Lives here because the task record and the attempt record both need it,
        and a rule written twice is a rule that drifts.
        """
        return None if self.ablated_skills else task.activates


def run(
    spec: EvalSpec,
    spec_path: Path,
    harness: HarnessBackend,
    judge: Judge,
    k: int = 3,
    workers: int = 4,
    timeout: int = 120,
    ablate: list[str] | None = None,
    on_attempt_done: Callable[[AttemptEvent], None] | None = None,
    on_task_done: Callable[[TaskResult], None] | None = None,
    fail_fast_unusable: int = 0,
    # Supplied by the CLI so it can surface the fetcher's warnings; defaulted
    # here so a caller with a path-only spec never has to think about it.
    fetcher: SkillFetcher | None = None,
    # Told when the backend reports running a different model than requested.
    on_warning: Callable[[str], None] | None = None,
) -> RunResults:
    # Before anything that can block: a Ctrl-C during skill fetching has to be
    # honoured by the attempts that would otherwise start right after it.
    cancel.reset()

    # Resolve the neighbourhood once, up front: a bad entry (a lone .md, a
    # missing frontmatter name:, a duplicate) should fail before any paid
    # attempt runs, not partway through.
    declared_refs = resolve_skills(
        list(spec.skills), spec_path.parent, fetcher=fetcher or SkillFetcher()
    )
    # Validated against the *declared* set, not the installed one: under
    # --ablate an `activates:` naming the removed skill has its expectation
    # dropped, not violated, so refusing it here would make a correct spec
    # unrunnable in exactly the mode it was written for. See
    # docs/adr/0015-ablation-names-its-subject-at-the-invocation.md.
    validate_activates(spec.tasks, declared_refs)
    # `--ablate` names a declared subject — a skill or an mcp: server — and the
    # resolution is what removes it from the run's environment. Duplicates
    # collapse here: `--ablate x --ablate x` removes one subject, and the marker
    # says so — it is the run's own description of what it did.
    ablation = apply_ablation(declared_refs, list(ablate or []), mcp_servers=spec.mcp)
    skill_refs = ablation.skill_refs

    # A spec's *surviving* mcp: servers configure the agent-under-test's tool
    # environment for the eval (a run-environment concern, like sandbox:). If the
    # chosen backend cannot materialize them, the declared tools would simply be
    # absent and every attempt would test something other than what the spec
    # claims — so refuse up front rather than silently drop them. Ablation
    # resolves before this guard, so a spec whose servers were all ablated is
    # runnable on a backend without MCP: their absence is then the user's
    # explicit choice, recorded in RunMeta.ablated rather than a silent drop.
    # This guard relaxes automatically as each backend flips ``supports_mcp``.
    if ablation.mcp_servers and not harness.supports_mcp:
        # A backend whose lack of MCP is permanent-by-design supplies its own
        # hint; the others get the generic "not yet" message. Either way we
        # refuse before any attempt rather than run with the declared tools
        # absent.
        if harness.mcp_unsupported_hint:
            raise HarnessConfigurationError(
                f"This eval declares mcp: servers, but the '{harness.name}' "
                "backend does not support MCP.\n\n" + harness.mcp_unsupported_hint
            )
        raise HarnessConfigurationError(
            f"This eval declares mcp: servers, but the '{harness.name}' backend does "
            "not support MCP yet. Only the 'claude-code' backend implements mcp: "
            "in this release.\n\n"
            "Re-run with --model claude-code (the default engine), or remove the "
            "mcp: block from the spec."
        )

    # Only the installed skills: a snapshot claims "this is what produced the
    # score", which an ablated skill demonstrably did not.
    skill_snapshots = [snapshot_skill(ref) for ref in skill_refs]
    detector = ActivationDetector(
        [ref.name for ref in skill_refs], harness.activation_tool_names
    )

    # Owns the whole forbidden-path rule, the spec's own entries and caliper's
    # additions alike (docs/CONTEXT.md → Sandbox).
    sandbox = SpecSandbox.from_spec(spec, spec_path)

    env = _RunEnv(
        harness=harness,
        judge=judge,
        sandbox=sandbox,
        activation=detector,
        spec=spec,
        spec_path=spec_path,
        skill_refs=skill_refs,
        mcp_servers=ablation.mcp_servers,
        # Field presence, not truthiness: an authored `mcp: {}` parses to an
        # empty mapping but still declares the block, and must isolate.
        mcp_declared="mcp" in spec.model_fields_set,
        ablated_skills=ablation.skill_names,
        timeout=timeout,
        fail_fast_unusable=fail_fast_unusable,
        on_attempt_done=on_attempt_done,
        on_task_done=on_task_done,
        resolved_models=[],
        judge_models=[],
        fatal=[],
        hook_failures=[],
    )

    # One pool job per *attempt*, not per task. Attempts are independent — each
    # gets its own isolated home and its own agent process — so scheduling them
    # per task capped a run's concurrency at the task count: `--workers 8` on a
    # 3-task spec ran three at a time, and a single-task spec ran its k attempts
    # strictly back to back. See
    # docs/adr/0018-the-attempt-is-the-unit-of-parallelism.md.
    #
    # `--fail-fast` is the one thing that couples a task's attempts (it counts
    # *consecutive* unusable ones, which needs an order), so that mode keeps a
    # per-task chain. Opting into fail-fast is opting into spending fewer
    # attempts, not into spending them faster.
    collected: dict[str, list[AttemptRecord]] = {task.id: [] for task in spec.tasks}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        if fail_fast_unusable > 0:
            futures = {
                pool.submit(_run_task_chain, task, env, k): task for task in spec.tasks
            }
        else:
            # Round-robin: attempt 1 of every task, then attempt 2 of every
            # task. Task-major submission would spend the whole budget on the
            # first tasks, so a run that stops early — a Ctrl-C, a rate limit —
            # would leave the last tasks with no attempts at all. This way a
            # partial run is a *shallower* sample of every task rather than a
            # complete one of a few, which is the sample you can still read.
            futures = {
                pool.submit(_run_attempt_job, task, attempt, env): task
                for attempt in range(1, k + 1)
                for task in spec.tasks
            }
        for fut in as_completed(futures):
            collected[futures[fut].id].extend(fut.result())

    task_results = [
        _finish_task(task, collected[task.id], env, k) for task in spec.tasks
    ]
    task_results.sort(key=lambda r: r.task_id)

    # Both scoreboards at once — they are two fields of one object, and the
    # activation half is never folded into avg_score (docs/adr/0014).
    aggregate = AggregateScore.from_task_results(
        task_results, k, declared=[ref.name for ref in skill_refs]
    )

    results = RunResults(
        run=RunMeta(
            spec=spec_name(spec_path),
            timestamp=datetime.now(tz=timezone.utc),
            k=k,
            # The engine is whatever the harness and judge were built with
            # (docs/adr/0004): asked of them, not passed in beside them.
            backend=harness.name,
            model=_recorded_model(harness.model, env.resolved_models, on_warning),
            judge_backend=judge.backend,
            # Prefer the judge's own model; else the concrete model an autorater
            # reported (e.g. claude-code). Stays None for assert-only runs, where
            # no LLM judge ran.
            judge_model=judge.model
            or (env.judge_models[0] if env.judge_models else None),
            era=ERA_INSTALL_AND_DISCOVER,
            ablated=ablation.names,
            # What the run's tool environment actually held, so a saved run
            # describes itself and `compare` can check an `mcp:` marker against
            # it rather than trusting the marker alone.
            mcp_servers=sorted(ablation.mcp_servers),
            # True when attempts were left unrun: Ctrl-C, or a fatal error the
            # run stopped for. Deliberately not inferred from a short attempt
            # list, which fail-fast also produces on purpose.
            interrupted=cancel.requested(),
            hook_failures=sorted(
                env.hook_failures,
                key=lambda failure: (
                    failure.task_id,
                    failure.attempt,
                    0 if failure.phase == "setup" else 1,
                ),
            ),
        ),
        skill_snapshots=skill_snapshots,
        task_results=task_results,
        aggregate=aggregate,
    )
    # Assembled first, raised second: the caller saves the partial run off the
    # exception before it surfaces the cause.
    if env.fatal:
        raise RunAborted(env.fatal[0], results)
    return results


def _attempt_or_none(
    task: TaskSpec, attempt: int, env: _RunEnv
) -> AttemptRecord | None:
    """One attempt, or ``None`` when it produced nothing worth recording.

    ``None`` covers the two ways an attempt can fail to be evidence: it never
    started because the run is stopping, or a cancellation killed it mid-flight.
    Neither is an observation about the skill, and inventing an outcome for
    either would put an artefact of the interrupt into the sample.

    Note what is *not* here: an attempt that failed on its own — a real timeout,
    a genuine infra error — is kept even when the run is being cancelled around
    it. Dropping on the outcome would have discarded exactly the evidence of the
    storm you were interrupting.
    """
    if cancel.requested():
        return None
    try:
        record = _run_attempt(task, attempt, env)
        if record is None:
            return None
        _announce(record, task, env.on_attempt_done)
        return record
    except (HarnessConfigurationError, SpendingCapReached) as exc:
        # Two different diagnoses, one response: a misconfiguration found
        # mid-run (an expired credential, a CLI that stopped resolving) and a
        # spending cap are both fatal for every attempt still to come, because
        # neither would behave differently on the next invocation. Stop the run
        # — but keep what it already paid for. ``run`` re-raises the first one
        # as ``RunAborted`` once the partial results are assembled.
        env.fatal.append(exc)
        cancel.request()
        return None


def _run_attempt_job(task: TaskSpec, attempt: int, env: _RunEnv) -> list[AttemptRecord]:
    """One attempt as a pool job — a list of zero or one, matching the chain.

    Both schedules hand the collector the same shape, so it needs no branch;
    which task a job belongs to is known from the future, not carried in the
    result.
    """
    record = _attempt_or_none(task, attempt, env)
    return [record] if record is not None else []


def _recorded_model(
    requested: str | None,
    resolved: list[str],
    on_warning: Callable[[str], None] | None,
) -> str | None:
    """The model ``RunMeta`` names: what the backend reported running (#131).

    Requested is only the fallback, for a backend that reports nothing. Trusting
    it first let a backend that ignored ``--model`` save a run claiming a model
    that never ran.
    """
    if not resolved:
        return requested
    actual = resolved[0]
    if requested and actual != requested and on_warning:
        on_warning(
            f"Requested model {requested!r}, but the backend reported running "
            f"{actual!r}; the run records {actual!r}."
        )
    return actual


def _run_task_chain(task: TaskSpec, env: _RunEnv, k: int) -> list[AttemptRecord]:
    """A task's k attempts in order, stopping on the fail-fast streak.

    The `--fail-fast` schedule. Sequential by necessity: the streak counts
    *consecutive* unusable attempts, and "consecutive" is not defined over
    attempts running side by side.

    Only ever called with ``env.fail_fast_unusable > 0`` — the run seam picks
    this schedule precisely because a threshold was set. The streak check below
    relies on it: at 0 it would break after the first attempt.
    """
    collected: list[AttemptRecord] = []
    consecutive_fail_fast_triggers = 0
    for attempt_num in range(1, k + 1):
        record = _attempt_or_none(task, attempt_num, env)
        if record is None:
            # Cancelled, or a fatal error stopped the run. Either way the
            # remaining attempts of this task are not going to run.
            break
        collected.append(record)
        if record.outcome in _FAIL_FAST_OUTCOMES:
            consecutive_fail_fast_triggers += 1
        elif not record.outcome.is_execution_noise:
            # Any healthy attempt breaks the streak — including a NOT_CHECKED
            # trigger probe, which ran fine and yielded a real activation
            # observation. Leaving it neutral would let a run abort mid-way and
            # silently truncate the activation sample. `judge_error` is noise and
            # still does not reset (see docs/adr/0001).
            consecutive_fail_fast_triggers = 0
        if consecutive_fail_fast_triggers >= env.fail_fast_unusable:
            break
    return collected


def _finish_task(
    task: TaskSpec, records: list[AttemptRecord], env: _RunEnv, k: int
) -> TaskResult:
    """Score one task's attempts into its result.

    Sorted by attempt number: under the flat schedule they finish in whatever
    order the pool hands them back, and a results file whose attempts are out of
    order is unreadable next to a transcript. A task can also land here with
    *fewer* than k records — fail-fast truncated it, or an interrupt stopped the
    run — which the metrics already handle, since every denominator is the
    usable attempts rather than k (docs/adr/0007).

    Nothing is counted here: the result derives its own successes, denominator
    and metrics from the attempts it is handed.
    """
    result = TaskResult(
        task_id=task.id,
        task_name=task.name,
        attempts=sorted(records, key=lambda r: r.attempt),
        activation_expected=env.expected_activation(task),
    )
    if env.on_task_done and len(result.attempts) < k:
        env.on_task_done(result)
    return result


def _run_attempt(task: TaskSpec, attempt: int, env: _RunEnv) -> AttemptRecord | None:
    """Run one attempt end to end: its lifecycle here, its verdict next door.

    This function owns what an attempt *costs* — a fresh isolated home, the
    task's setup/cleanup shell, one harness invocation — and hands the finished
    result to :func:`caliper.attempt.assemble_attempt`, which owns what it
    *means*.
    """
    tmp_dir = tempfile.mkdtemp(prefix="caliper-")
    failures: list[HookFailure] = []
    record: AttemptRecord | None = None
    try:
        setup = _run_shell(task.setup, task.id, attempt, "setup")
        if setup is not None:
            failures.append(setup)
            record = AttemptRecord(
                attempt=attempt,
                output="",
                duration_seconds=0.0,
                outcome=Outcome.INFRA_ERROR,
                assert_evidence=f"setup exited {setup.exit_code}",
            )
        elif not cancel.requested():
            record = _measure_attempt(task, attempt, env, tmp_dir)
    finally:
        cleanup = _run_shell(task.cleanup, task.id, attempt, "cleanup")
        if cleanup is not None:
            failures.append(cleanup)
        env.hook_failures.extend(failures)
        shutil.rmtree(tmp_dir, ignore_errors=True)
    if record is not None:
        record.hook_failures.extend(failures)
    return record


def _measure_attempt(
    task: TaskSpec, attempt: int, env: _RunEnv, tmp_dir: str
) -> AttemptRecord | None:
    spec, spec_path = env.spec, env.spec_path

    resolved_extra_path = [
        str((spec_path.parent / p).resolve()) for p in spec.sandbox.extra_path
    ]

    # The neighbourhood is *installed* by the harness at its own skills root
    # and never preloaded. ``env.skill_refs`` is already the ablated set.
    def invoke():
        # Built inside the closure, so a retried attempt gets its own
        # context rather than the previous invocation's scratch
        # (docs/adr/0019 — the attempt is the shot, not the spawn).
        return env.harness.run(
            RunContext(
                task_id=task.id,
                attempt=attempt,
                prompt=task.prompt,
                skill_refs=env.skill_refs,
                # None → the harness uses the model it was constructed with;
                # the engine is resolved once at the run seam (ADR 0004),
                # not per spec.
                model=None,
                timeout=env.timeout,
                isolated_home=tmp_dir,
                extra_path=resolved_extra_path,
                # Declared MCP servers are the agent's tool environment for
                # the eval; the backend materializes them. Already reduced by
                # any ``--ablate``. ``None`` only when the spec declared no
                # ``mcp:`` block; an empty mapping is a declared block whose
                # servers were all ablated, and still isolates the attempt.
                mcp_servers=env.mcp_servers if env.mcp_declared else None,
                forbidden_files=list(spec.sandbox.forbidden_files),
            )
        )

    # A throttled invocation measured nothing, so it is retried rather than
    # recorded — the attempt is the shot at the task, not the spawn
    # (docs/adr/0019). The retry holds this worker: under throttling there is
    # no other work to give the slot, since every peer is meeting the same
    # 429. Raises SpendingCapReached, which the job above turns into a run
    # abort.
    invoked = invoke_with_retry(invoke)
    attempt_result = invoked.result
    if attempt_result.resolved_model:
        env.resolved_models.append(attempt_result.resolved_model)

    # Killed by the cancellation, not by anything about the skill. Returned
    # as nothing at all rather than assembled into an infra_error — the one
    # place that can tell the two apart, because only the spawn knows who
    # killed it.
    if attempt_result.cancelled:
        return None

    assembled = assemble_attempt(
        attempt_result,
        attempt=attempt,
        task=task,
        spec_dir=str(spec_path.parent),
        expected_activation=env.expected_activation(task),
        activation=env.activation,
        sandbox=env.sandbox,
        judge=env.judge,
        retries=invoked.retries,
    )
    if assembled.judge_model:
        env.judge_models.append(assembled.judge_model)

    return assembled.record


def _announce(
    record: AttemptRecord,
    task: TaskSpec,
    on_attempt_done: Callable[[AttemptEvent], None] | None,
) -> None:
    """Tell the caller an attempt landed — only for records that count.

    Fired from the job rather than from ``_run_attempt`` so a record the run
    then discards is never announced: a live ``⊘ infra_error`` printed for an
    attempt the user's own Ctrl-C killed reads as a problem with the eval.
    """
    if on_attempt_done:
        on_attempt_done(
            AttemptEvent(
                task_id=task.id, attempt=record.attempt, outcome=record.outcome
            )
        )


def _run_shell(
    cmd: str | None, task_id: str, attempt: int, phase: HookPhase
) -> HookFailure | None:
    if not cmd:
        return None
    # Drain while the shell runs so verbose output cannot fill a pipe. Stop
    # when *that shell* exits: background children may keep its pipe open or
    # write forever, and must not delay the next attempt or retain disk space.
    tail = bytearray()

    def keep(chunk: bytes) -> None:
        tail.extend(chunk)
        if len(tail) > 16000:
            del tail[:-16000]

    with (
        subprocess.Popen(
            cmd,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        ) as process,
        cancel.track(process, cancel_if_requested=phase != "cleanup"),
    ):
        assert process.stdout is not None
        fd = process.stdout.fileno()
        if os.name == "nt":
            # Windows select() cannot watch pipes. A reader thread drains until
            # the shell exits; it never holds a disk-backed output file open.
            stopped = threading.Event()

            def drain() -> None:
                while not stopped.is_set():
                    try:
                        chunk = os.read(fd, 8192)
                    except OSError:
                        break
                    if not chunk:
                        break
                    keep(chunk)

            reader = threading.Thread(
                target=drain, name="caliper-hook-output", daemon=True
            )
            reader.start()
            process.wait()
            reader.join(timeout=0.1)
            if reader.is_alive():
                stopped.set()
                # Closing a pipe does not reliably interrupt another thread's
                # synchronous ReadFile on Windows. Cancel that read explicitly
                # before closing the stream, then wait for the thread to exit.
                import ctypes
                from ctypes import wintypes

                kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
                kernel32.OpenThread.argtypes = (
                    wintypes.DWORD,
                    wintypes.BOOL,
                    wintypes.DWORD,
                )
                kernel32.OpenThread.restype = wintypes.HANDLE
                kernel32.CancelSynchronousIo.argtypes = (wintypes.HANDLE,)
                kernel32.CancelSynchronousIo.restype = wintypes.BOOL
                kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
                kernel32.CloseHandle.restype = wintypes.BOOL
                thread_handle = kernel32.OpenThread(0x0001, False, reader.native_id)
                if thread_handle:
                    try:
                        kernel32.CancelSynchronousIo(thread_handle)
                    finally:
                        kernel32.CloseHandle(thread_handle)
                process.stdout.close()
                reader.join(timeout=1)
                if reader.is_alive():
                    raise RuntimeError("Could not stop lifecycle hook output reader")
        else:
            while process.poll() is None:
                if select.select([fd], [], [], 0.1)[0]:
                    chunk = os.read(fd, 8192)
                    if chunk:
                        keep(chunk)
                    else:
                        process.wait()
                        break
            # Drain bytes already available, with a limit so a continuously
            # writing descendant cannot keep us here indefinitely.
            for _ in range(128):
                if not select.select([fd], [], [], 0)[0]:
                    break
                chunk = os.read(fd, 8192)
                if not chunk:
                    break
                keep(chunk)
        exit_code = process.returncode
        was_killed = cancel.was_killed(process)
    if was_killed:
        return None
    if exit_code == 0:
        return None
    output = tail.decode("utf-8", errors="replace").strip()
    return HookFailure(
        task_id=task_id,
        attempt=attempt,
        phase=phase,
        exit_code=exit_code,
        output=output[-4000:],
    )
