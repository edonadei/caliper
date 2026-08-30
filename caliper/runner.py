from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from caliper import cancel
from caliper.activation import ActivationDetector
from caliper.attempt import assemble_attempt
from caliper.harness.base import (
    ConversationTurn,
    HarnessBackend,
    HarnessConfigurationError,
)
from caliper.judge.base import Judge
from caliper.retry import SpendingCapReached, invoke_with_retry
from caliper.runstore import RunStore
from caliper.schema.results import (
    ERA_INSTALL_AND_DISCOVER,
    AttemptRecord,
    Outcome,
    RunMeta,
    RunResults,
    TaskResult,
)
from caliper.schema.spec import DEFAULT_BACKEND, EvalSpec, TaskSpec, spec_name
from caliper.scoring import aggregate_activation, aggregate_scores
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
    cheat: _CheatDetector
    activation: ActivationDetector
    spec: EvalSpec
    spec_path: Path
    # The skills actually installed: the declared neighbourhood minus anything
    # ``--ablate`` removed.
    skill_refs: list[SkillRef]
    # Truthy on an ablated run, which drops every task's activation expectation.
    ablated: list[str]
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

    def expected_activation(self, task: TaskSpec) -> list[str] | None:
        """What this run asserts the task should activate — ``None`` if ablated.

        An ablated run **drops** the expectation rather than filtering the
        removed skill out of it. Filtering would assert a claim the author never
        wrote, and it inverts the delegating case: remove a parent and its
        neighbours correctly stop firing, so scoring that as a miss would report
        the finding as a failure. The observation is still recorded; only the
        verdict is withheld, so the column renders skipped rather than 0%. See
        docs/adr/0015-ablation-names-its-subject-at-the-invocation.md.

        Lives here because the task record and the attempt record both need it,
        and a rule written twice is a rule that drifts.
        """
        return None if self.ablated else task.activates


def run(
    spec: EvalSpec,
    spec_path: Path,
    harness: HarnessBackend,
    judge: Judge,
    backend: str = DEFAULT_BACKEND,
    model: str | None = None,
    judge_backend: str | None = None,
    judge_model: str | None = None,
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
) -> RunResults:
    # Before anything that can block: a Ctrl-C during skill fetching has to be
    # honoured by the attempts that would otherwise start right after it.
    cancel.reset()

    # A spec's mcp: servers configure the agent-under-test's tool environment
    # for the eval (a run-environment concern, like sandbox:). If the chosen
    # backend cannot materialize them, the declared tools would simply be
    # absent and every attempt would test something other than what the spec
    # claims — so refuse up front rather than silently drop them. This guard
    # relaxes automatically as each backend flips ``supports_mcp`` to True.
    if spec.mcp and not harness.supports_mcp:
        # A backend whose lack of MCP is permanent-by-design supplies its own
        # hint; the others get the generic "not yet" message. Either way we
        # refuse before any attempt rather than run with the declared tools
        # absent.
        if harness.mcp_unsupported_hint:
            raise HarnessConfigurationError(
                f"This eval declares mcp: servers, but the '{backend}' backend "
                "does not support MCP.\n\n" + harness.mcp_unsupported_hint
            )
        raise HarnessConfigurationError(
            f"This eval declares mcp: servers, but the '{backend}' backend does "
            "not support MCP yet. Only the 'claude-code' backend implements mcp: "
            "in this release.\n\n"
            "Re-run with --model claude-code (the default engine), or remove the "
            "mcp: block from the spec."
        )

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
    # Deduplicated: `--ablate x --ablate x` removes one skill, and the marker
    # says so — it is the run's own description of what it did.
    ablated = sorted(set(ablate or []))
    skill_refs = apply_ablation(declared_refs, ablated)

    # Only the installed skills: a snapshot claims "this is what produced the
    # score", which an ablated skill demonstrably did not.
    skill_snapshots = [snapshot_skill(ref) for ref in skill_refs]
    detector = ActivationDetector(
        [ref.name for ref in skill_refs], harness.activation_tool_names
    )

    auto_forbidden = [
        re.escape(str(spec_path.resolve())),
        re.escape(str(RunStore(spec_path.parent).caliper_dir.resolve())),
    ]
    cheat = _CheatDetector(list(spec.sandbox.forbidden_files) + auto_forbidden)

    env = _RunEnv(
        harness=harness,
        judge=judge,
        cheat=cheat,
        activation=detector,
        spec=spec,
        spec_path=spec_path,
        skill_refs=skill_refs,
        ablated=ablated,
        timeout=timeout,
        fail_fast_unusable=fail_fast_unusable,
        on_attempt_done=on_attempt_done,
        on_task_done=on_task_done,
        resolved_models=[],
        judge_models=[],
        fatal=[],
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

    aggregate = aggregate_scores(task_results, k)
    # The second scoreboard, carried alongside — never folded into avg_score.
    activation = aggregate_activation(task_results, [ref.name for ref in skill_refs])
    aggregate.avg_activation_score = activation.avg_score
    aggregate.activation_tasks = activation.tasks
    aggregate.activation_asserted = activation.asserted
    aggregate.activation_per_skill = activation.per_skill

    results = RunResults(
        run=RunMeta(
            spec=spec_name(spec_path),
            timestamp=datetime.now(tz=timezone.utc),
            k=k,
            backend=backend,
            # Prefer the explicitly requested model; otherwise fall back to the
            # concrete model an attempt resolved (e.g. from hermes' export), so a
            # default-model run still records what actually ran.
            model=model or (env.resolved_models[0] if env.resolved_models else None),
            judge_backend=judge_backend,
            # Prefer the explicitly requested judge model; else the concrete model
            # an autorater reported (e.g. claude-code). Stays None for assert-only
            # runs, where no LLM judge ran.
            judge_model=judge_model
            or (env.judge_models[0] if env.judge_models else None),
            era=ERA_INSTALL_AND_DISCOVER,
            ablated=ablated,
            # True when attempts were left unrun: Ctrl-C, or a fatal error the
            # run stopped for. Deliberately not inferred from a short attempt
            # list, which fail-fast also produces on purpose.
            interrupted=cancel.requested(),
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
    spec, spec_path = env.spec, env.spec_path
    tmp_dir = tempfile.mkdtemp(prefix="caliper-")
    try:
        _run_shell(task.setup)
        resolved_extra_path = [
            str((spec_path.parent / p).resolve()) for p in spec.sandbox.extra_path
        ]

        # The neighbourhood is *installed* by the harness at its own skills root
        # and never preloaded. ``env.skill_refs`` is already the ablated set.
        def invoke():
            return env.harness.run(
                task_id=task.id,
                attempt=attempt,
                prompt=task.prompt,
                skill_refs=env.skill_refs,
                # None → the harness uses the model it was constructed with; the
                # engine is resolved once at the run seam (ADR 0004), not per spec.
                model=None,
                timeout=env.timeout,
                isolated_home=tmp_dir,
                extra_path=resolved_extra_path,
                # Declared MCP servers are the agent's tool environment for the
                # eval; the backend materializes them. ``None`` when none declared.
                mcp_servers=dict(spec.mcp) or None,
                forbidden_files=list(spec.sandbox.forbidden_files),
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
            cheat=env.cheat,
            judge=env.judge,
            retries=invoked.retries,
        )
        if assembled.judge_model:
            env.judge_models.append(assembled.judge_model)

        return assembled.record
    finally:
        _run_shell(task.cleanup)
        shutil.rmtree(tmp_dir, ignore_errors=True)


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


def _run_shell(cmd: str | None) -> None:
    if cmd:
        subprocess.run(cmd, shell=True, check=False)


class _CheatDetector:
    def __init__(self, patterns: list[str]) -> None:
        self._compiled = [re.compile(p) for p in patterns]

    def check(self, transcript: list[ConversationTurn]) -> list[str]:
        violations: list[str] = []
        for turn in transcript:
            if turn.tool_input:
                for value in self._extract_paths(turn.tool_input):
                    if any(r.search(value) for r in self._compiled):
                        violations.append(value)
        return violations

    def _extract_paths(self, obj: dict | list | str, depth: int = 0) -> list[str]:
        if depth > 5:
            return []
        if isinstance(obj, str):
            return [obj] if ("/" in obj or "." in obj) else []
        if isinstance(obj, dict):
            results: list[str] = []
            for v in obj.values():
                results.extend(self._extract_paths(v, depth + 1))
            return results
        if isinstance(obj, list):
            results = []
            for item in obj:
                results.extend(self._extract_paths(item, depth + 1))
            return results
        return []
