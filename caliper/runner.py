from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from caliper.activation import ActivationDetector, check_activation
from caliper.harness.base import (
    ConversationTurn,
    HarnessBackend,
    HarnessConfigurationError,
)
from caliper.judge.base import Judge
from caliper.outcome import classify_outcome, classify_pre_judge
from caliper.schema.results import (
    ERA_INSTALL_AND_DISCOVER,
    AttemptRecord,
    FileSnapshot,
    Outcome,
    RunMeta,
    RunResults,
    SkillSnapshot,
    TaskResult,
    TranscriptTurn,
)
from caliper.schema.spec import DEFAULT_BACKEND, EvalSpec, TaskSpec, spec_name
from caliper.scoring import aggregate_activation, aggregate_scores, score_outcomes
from caliper.skills import SkillRef, resolve_skills

_FAIL_FAST_OUTCOMES = {Outcome.INFRA_ERROR, Outcome.TIMEOUT}


@dataclass
class AttemptEvent:
    task_id: str
    attempt: int
    outcome: Outcome


@dataclass(frozen=True)
class _RunEnv:
    """Everything constant across a run's tasks and attempts.

    Threaded as one value so ``_run_task``/``_run_attempt`` keep a readable
    signature — they vary only by task, attempt number, and whether the skills
    are installed (the ``--baseline`` axis).
    """

    harness: HarnessBackend
    judge: Judge
    cheat: _CheatDetector
    detector: ActivationDetector
    spec: EvalSpec
    spec_path: Path
    skill_refs: list[SkillRef]
    timeout: int
    fail_fast_unusable: int
    on_attempt_done: Callable[[AttemptEvent], None] | None
    on_task_done: Callable[[TaskResult], None] | None
    # Collect the concrete model each attempt/judge call resolved, so RunMeta can
    # record what really ran even on a CLI default. list.append is atomic under
    # the GIL, so these are safe to share across the pool's worker threads.
    resolved_models: list[str]
    judge_models: list[str]


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
    baseline: bool = False,
    on_attempt_done: Callable[[AttemptEvent], None] | None = None,
    on_task_done: Callable[[TaskResult], None] | None = None,
    fail_fast_unusable: int = 0,
) -> RunResults:
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
    skill_refs = resolve_skills(list(spec.skills), spec_path.parent)
    _validate_activates(spec, skill_refs)

    snapshotter = _SkillSnapshotter()
    skill_snapshots = [
        snapshotter.snapshot(str(ref.path), ref.name) for ref in skill_refs
    ]
    detector = ActivationDetector(
        [ref.name for ref in skill_refs], harness.activation_tool_names
    )

    auto_forbidden = [
        re.escape(str(spec_path.resolve())),
        re.escape(str((spec_path.parent / ".caliper").resolve())),
    ]
    cheat = _CheatDetector(list(spec.sandbox.forbidden_files) + auto_forbidden)

    task_results_with: list[TaskResult] = []
    task_results_without: list[TaskResult] = []

    env = _RunEnv(
        harness=harness,
        judge=judge,
        cheat=cheat,
        detector=detector,
        spec=spec,
        spec_path=spec_path,
        skill_refs=skill_refs,
        timeout=timeout,
        fail_fast_unusable=fail_fast_unusable,
        on_attempt_done=on_attempt_done,
        on_task_done=on_task_done,
        resolved_models=[],
        judge_models=[],
    )

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures_with = {
            pool.submit(_run_task, task, env, k, True): task for task in spec.tasks
        }
        futures_without = (
            {pool.submit(_run_task, task, env, k, False): task for task in spec.tasks}
            if baseline
            else {}
        )

        for fut in as_completed(list(futures_with) + list(futures_without)):
            result = fut.result()
            if fut in futures_with:
                task_results_with.append(result)
            else:
                task_results_without.append(result)

    task_results_with.sort(key=lambda r: r.task_id)
    task_results_without.sort(key=lambda r: r.task_id)

    pass_counts_with = {
        r.task_id: (r.task_name, r.successes, r.usable, k) for r in task_results_with
    }
    agg_with = aggregate_scores(pass_counts_with)
    # The second scoreboard, carried alongside — never folded into avg_score.
    activation = aggregate_activation(task_results_with)
    agg_with.avg_activation_score = activation.avg_score
    agg_with.activation_tasks = activation.tasks
    agg_with.activation_per_skill = activation.per_skill

    # Keep the whole no-skill run so the report can render it through the same
    # ``compare`` path as any other two-run diff (see reporter.diff_baseline).
    baseline_task_results = (
        task_results_without if baseline and task_results_without else None
    )

    return RunResults(
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
        ),
        skill_snapshots=skill_snapshots,
        task_results=task_results_with,
        aggregate=agg_with,
        baseline_task_results=baseline_task_results,
    )


def _run_task(task: TaskSpec, env: _RunEnv, k: int, with_skill: bool) -> TaskResult:
    attempts: list[AttemptRecord] = []
    consecutive_fail_fast_triggers = 0
    for attempt_num in range(1, k + 1):
        record = _run_attempt(task, attempt_num, env, with_skill)
        attempts.append(record)
        if record.outcome in _FAIL_FAST_OUTCOMES:
            consecutive_fail_fast_triggers += 1
        elif record.outcome.is_usable:
            consecutive_fail_fast_triggers = 0
        if (
            env.fail_fast_unusable > 0
            and consecutive_fail_fast_triggers >= env.fail_fast_unusable
        ):
            break

    scores = score_outcomes(a.outcome for a in attempts)
    result = TaskResult(
        task_id=task.id,
        task_name=task.name,
        attempts=attempts,
        successes=scores.successes,
        unusable=scores.unusable,
        pass_at_k=scores.pass_at_k,
        # A --baseline arm installs no skills, so scoring activation there would
        # be scoring caliper's own plumbing: the expectation is dropped, and the
        # column renders skipped rather than 0%.
        activation_expected=task.activates if with_skill else None,
    )
    if env.on_task_done and len(attempts) < k:
        env.on_task_done(result)
    return result


def _persist_transcript(
    turns: list[ConversationTurn],
) -> list[TranscriptTurn]:
    return [TranscriptTurn(**asdict(turn)) for turn in turns]


def _run_attempt(
    task: TaskSpec, attempt: int, env: _RunEnv, with_skill: bool
) -> AttemptRecord:
    spec, spec_path = env.spec, env.spec_path
    tmp_dir = tempfile.mkdtemp(prefix="caliper-")
    try:
        _run_shell(task.setup)
        resolved_extra_path = [
            str((spec_path.parent / p).resolve()) for p in spec.sandbox.extra_path
        ]
        # The neighbourhood is *installed* by the harness at its own skills root
        # and never preloaded; --baseline installs nothing at all.
        skill_refs = env.skill_refs if with_skill else []
        attempt_result = env.harness.run(
            task_id=task.id,
            attempt=attempt,
            prompt=task.prompt,
            skill_refs=skill_refs,
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
        if attempt_result.resolved_model:
            env.resolved_models.append(attempt_result.resolved_model)

        # Recorded on *every* attempt, asserted only when the task said so. A
        # --baseline arm installed nothing, so there was no choice to observe.
        activated = (
            env.detector.detect(attempt_result.transcript) if with_skill else None
        )
        expected = task.activates if with_skill else None
        activation_passed = check_activation(activated, expected)

        def record(outcome: Outcome, **verdict) -> AttemptRecord:
            """One attempt's record; only the verdict fields differ per exit path.

            ``activated``/``activation_passed`` ride on every path — including
            the pre-judge exits — because activation is observed from the
            transcript and owes nothing to the judge.
            """
            return AttemptRecord(
                attempt=attempt,
                output=attempt_result.final_output,
                duration_seconds=attempt_result.duration_seconds,
                outcome=outcome,
                usage=attempt_result.usage,
                transcript=_persist_transcript(attempt_result.transcript),
                activated=activated,
                activation_passed=activation_passed,
                **verdict,
            )

        # A timeout or infrastructure signal terminates the attempt before we
        # spend a (paid) judge call on garbage output. The pre-judge classifier
        # owns that predicate — the runner no longer re-derives it — so the skip
        # here and the final label can never drift apart.
        pre_judge_outcome = classify_pre_judge(attempt_result)
        if pre_judge_outcome is not None:
            evidence = (
                attempt_result.error or f"harness exited {attempt_result.exit_code}"
            )
            return _finish(
                record(pre_judge_outcome, assert_evidence=evidence),
                task,
                env.on_attempt_done,
            )

        cheat_violations = env.cheat.check(attempt_result.transcript)
        if cheat_violations:
            outcome = classify_outcome(attempt_result, cheat_violations, None)
            return _finish(
                record(outcome, cheat_evidence=cheat_violations),
                task,
                env.on_attempt_done,
            )

        # An `activates:`-only task authored no execution check, so there is
        # nothing to grade — skip the (paid) judge call rather than spending it
        # to receive a non-verdict and label the attempt an error.
        if not (task.expect or task.assert_script):
            return _finish(
                record(
                    classify_outcome(
                        attempt_result, [], None, has_execution_check=False
                    )
                ),
                task,
                env.on_attempt_done,
            )

        judge_result = env.judge.evaluate(
            task=task,
            transcript=attempt_result.transcript,
            final_output=attempt_result.final_output,
            spec_dir=str(spec_path.parent),
        )
        if judge_result.resolved_model:
            env.judge_models.append(judge_result.resolved_model)
        outcome = classify_outcome(attempt_result, [], judge_result)

        return _finish(
            record(
                outcome,
                assert_passed=judge_result.assert_passed,
                assert_evidence=judge_result.assert_evidence,
                autorater_passed=judge_result.autorater_passed,
                autorater_reasoning=judge_result.autorater_reasoning,
            ),
            task,
            env.on_attempt_done,
        )
    finally:
        _run_shell(task.cleanup)
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _finish(
    record: AttemptRecord,
    task: TaskSpec,
    on_attempt_done: Callable[[AttemptEvent], None] | None,
) -> AttemptRecord:
    if on_attempt_done:
        on_attempt_done(
            AttemptEvent(
                task_id=task.id, attempt=record.attempt, outcome=record.outcome
            )
        )
    return record


def _run_shell(cmd: str | None) -> None:
    if cmd:
        subprocess.run(cmd, shell=True, check=False)


def _validate_activates(spec: EvalSpec, skill_refs: list[SkillRef]) -> None:
    """Refuse an ``activates:`` naming a skill the spec never declared.

    The neighbourhood is closed: an undeclared skill is not installed and so can
    *never* activate, which would make the expectation unsatisfiable — a task
    stuck at 0% for a reason no transcript explains. Catch it before the run
    spends anything.
    """
    declared = {ref.name for ref in skill_refs}
    for task in spec.tasks:
        unknown = [name for name in (task.activates or []) if name not in declared]
        if not unknown:
            continue
        listed = ", ".join(sorted(declared)) or "(none)"
        raise HarnessConfigurationError(
            f"Task '{task.name}' expects {', '.join(unknown)} to activate, but "
            f"the spec's skills: declares only {listed}.\n\n"
            "An undeclared skill is never installed, so it cannot activate and "
            "the expectation could never be met. Add it to skills:, or correct "
            "the name (identity is the frontmatter name:, not the filename)."
        )


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


class _SkillSnapshotter:
    _REF_PATTERN = re.compile(r'[./~][^\s"\'<>]+\.(sh|py|md|js|ts)')

    def snapshot(self, skill_path: str | None, name: str = "") -> SkillSnapshot:
        if not skill_path:
            return SkillSnapshot(name=name, path="", files={})

        path = Path(skill_path).expanduser().resolve()
        if not path.exists():
            return SkillSnapshot(name=name, path=str(path), files={})

        files: dict[str, FileSnapshot] = {}
        content = path.read_text()
        files[path.name] = FileSnapshot(
            content=content,
            hash="sha256:" + hashlib.sha256(content.encode()).hexdigest(),
        )

        for match in self._REF_PATTERN.finditer(content):
            ref = Path(match.group()).expanduser()
            if not ref.is_absolute():
                ref = path.parent / ref
            ref = ref.resolve()
            if ref.exists() and ref != path:
                rel = str(ref.relative_to(path.parent))
                ref_content = ref.read_text()
                files[rel] = FileSnapshot(
                    content=ref_content,
                    hash="sha256:" + hashlib.sha256(ref_content.encode()).hexdigest(),
                )

        git_repo, git_sha = self._git_info(path)
        return SkillSnapshot(
            name=name,
            path=str(path),
            git_repo=git_repo,
            git_sha=git_sha,
            files=files,
        )

    def _git_info(self, path: Path) -> tuple[str | None, str | None]:
        try:
            repo = subprocess.check_output(
                ["git", "rev-parse", "--show-toplevel"],
                cwd=str(path.parent),
                stderr=subprocess.DEVNULL,
                text=True,
            ).strip()
            sha = subprocess.check_output(
                ["git", "rev-parse", "HEAD"],
                cwd=str(path.parent),
                stderr=subprocess.DEVNULL,
                text=True,
            ).strip()
            return repo, sha
        except (subprocess.CalledProcessError, FileNotFoundError):
            return None, None
