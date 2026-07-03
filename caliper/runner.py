from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from caliper.harness.base import ConversationTurn, HarnessBackend
from caliper.judge.base import Judge
from caliper.outcome import classify_outcome, classify_pre_judge
from caliper.schema.results import (
    AggregateScore,
    AttemptRecord,
    DeltaReport,
    FileSnapshot,
    Outcome,
    RunMeta,
    RunResults,
    SkillSnapshot,
    TaskResult,
)
from caliper.schema.spec import DEFAULT_BACKEND, EvalSpec, TaskSpec, spec_name
from caliper.scoring import aggregate_scores, compute_delta, pass_at_k

_FAIL_FAST_OUTCOMES = {Outcome.INFRA_ERROR, Outcome.TIMEOUT}


@dataclass
class AttemptEvent:
    task_id: str
    attempt: int
    outcome: Outcome


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
    skill_snapshot = _SkillSnapshotter().snapshot(_resolve_skill_path(spec, spec_path))

    auto_forbidden = [
        re.escape(str(spec_path.resolve())),
        re.escape(str((spec_path.parent / ".caliper").resolve())),
    ]
    cheat = _CheatDetector(list(spec.sandbox.forbidden_files) + auto_forbidden)

    task_results_with: list[TaskResult] = []
    task_results_without: list[TaskResult] = []
    # Collects the concrete model each attempt resolved (same value every time),
    # so RunMeta can record the real model even when the CLI's default was used.
    # list.append is atomic under the GIL, so it is safe across worker threads.
    resolved_models: list[str] = []
    # Same, for the judge autorater's concrete model (only set for expect: tasks
    # whose judge CLI reports it, e.g. claude-code).
    judge_models: list[str] = []

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures_with = {
            pool.submit(
                _run_task,
                task,
                harness,
                judge,
                cheat,
                spec,
                spec_path,
                k,
                timeout,
                True,
                on_attempt_done,
                on_task_done,
                fail_fast_unusable,
                resolved_models,
                judge_models,
            ): task
            for task in spec.tasks
        }
        futures_without = (
            {
                pool.submit(
                    _run_task,
                    task,
                    harness,
                    judge,
                    cheat,
                    spec,
                    spec_path,
                    k,
                    timeout,
                    False,
                    on_attempt_done,
                    on_task_done,
                    fail_fast_unusable,
                    resolved_models,
                    judge_models,
                ): task
                for task in spec.tasks
            }
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
        r.task_id: (r.task_name, r.successes, len(r.attempts) - r.unusable, k)
        for r in task_results_with
    }
    agg_with = aggregate_scores(pass_counts_with)

    agg_without: AggregateScore | None = None
    delta: DeltaReport | None = None
    if baseline and task_results_without:
        pass_counts_without = {
            r.task_id: (r.task_name, r.successes, len(r.attempts) - r.unusable, k)
            for r in task_results_without
        }
        agg_without = aggregate_scores(pass_counts_without)
        delta = compute_delta(agg_with, agg_without)

    return RunResults(
        run=RunMeta(
            spec=spec_name(spec_path),
            timestamp=datetime.now(tz=timezone.utc),
            k=k,
            backend=backend,
            # Prefer the explicitly requested model; otherwise fall back to the
            # concrete model an attempt resolved (e.g. from hermes' export), so a
            # default-model run still records what actually ran.
            model=model or (resolved_models[0] if resolved_models else None),
            judge_backend=judge_backend,
            # Prefer the explicitly requested judge model; else the concrete model
            # an autorater reported (e.g. claude-code). Stays None for assert-only
            # runs, where no LLM judge ran.
            judge_model=judge_model or (judge_models[0] if judge_models else None),
        ),
        skill_snapshot=skill_snapshot,
        task_results=task_results_with,
        aggregate=agg_with,
        baseline=agg_without,
        delta=delta,
    )


def _run_task(
    task: TaskSpec,
    harness: HarnessBackend,
    judge: Judge,
    cheat: _CheatDetector,
    spec: EvalSpec,
    spec_path: Path,
    k: int,
    timeout: int,
    with_skill: bool,
    on_attempt_done: Callable[[AttemptEvent], None] | None,
    on_task_done: Callable[[TaskResult], None] | None,
    fail_fast_unusable: int,
    resolved_models: list[str],
    judge_models: list[str],
) -> TaskResult:
    attempts: list[AttemptRecord] = []
    consecutive_fail_fast_triggers = 0
    for attempt_num in range(1, k + 1):
        record = _run_attempt(
            task,
            attempt_num,
            harness,
            judge,
            cheat,
            spec,
            spec_path,
            timeout,
            with_skill,
            on_attempt_done,
            resolved_models,
            judge_models,
        )
        attempts.append(record)
        if record.outcome in _FAIL_FAST_OUTCOMES:
            consecutive_fail_fast_triggers += 1
        elif record.outcome.is_usable:
            consecutive_fail_fast_triggers = 0
        if (
            fail_fast_unusable > 0
            and consecutive_fail_fast_triggers >= fail_fast_unusable
        ):
            break

    successes = sum(1 for a in attempts if a.outcome == Outcome.PASS)
    usable = sum(1 for a in attempts if a.outcome.is_usable)
    unusable = len(attempts) - usable
    result = TaskResult(
        task_id=task.id,
        task_name=task.name,
        attempts=attempts,
        successes=successes,
        unusable=unusable,
        pass_at_k=pass_at_k(successes, usable) if usable > 0 else None,
    )
    if on_task_done and len(attempts) < k:
        on_task_done(result)
    return result


def _run_attempt(
    task: TaskSpec,
    attempt: int,
    harness: HarnessBackend,
    judge: Judge,
    cheat: _CheatDetector,
    spec: EvalSpec,
    spec_path: Path,
    timeout: int,
    with_skill: bool,
    on_attempt_done: Callable[[AttemptEvent], None] | None,
    resolved_models: list[str],
    judge_models: list[str],
) -> AttemptRecord:
    tmp_dir = tempfile.mkdtemp(prefix="caliper-")
    try:
        _run_shell(task.setup)
        resolved_extra_path = [
            str((spec_path.parent / p).resolve()) for p in spec.sandbox.extra_path
        ]
        skill_path = _resolve_skill_path(spec, spec_path) if with_skill else None
        if skill_path:
            _stage_skill_directory(
                skill_path, tmp_dir, list(spec.sandbox.forbidden_files)
            )
        attempt_result = harness.run(
            task_id=task.id,
            attempt=attempt,
            prompt=task.prompt,
            skill_path=skill_path,
            # None → the harness uses the model it was constructed with; the
            # engine is resolved once at the run seam (ADR 0004), not per spec.
            model=None,
            timeout=timeout,
            isolated_home=tmp_dir,
            extra_path=resolved_extra_path,
        )
        if attempt_result.resolved_model:
            resolved_models.append(attempt_result.resolved_model)

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
                AttemptRecord(
                    attempt=attempt,
                    output=attempt_result.final_output,
                    duration_seconds=attempt_result.duration_seconds,
                    outcome=pre_judge_outcome,
                    assert_evidence=evidence,
                ),
                task,
                on_attempt_done,
            )

        cheat_violations = cheat.check(attempt_result.transcript)
        if cheat_violations:
            outcome = classify_outcome(attempt_result, cheat_violations, None)
            return _finish(
                AttemptRecord(
                    attempt=attempt,
                    output=attempt_result.final_output,
                    duration_seconds=attempt_result.duration_seconds,
                    outcome=outcome,
                    cheat_evidence=cheat_violations,
                ),
                task,
                on_attempt_done,
            )

        judge_result = judge.evaluate(
            task=task,
            transcript=attempt_result.transcript,
            final_output=attempt_result.final_output,
            spec_dir=str(spec_path.parent),
        )
        if judge_result.resolved_model:
            judge_models.append(judge_result.resolved_model)
        outcome = classify_outcome(attempt_result, [], judge_result)

        return _finish(
            AttemptRecord(
                attempt=attempt,
                output=attempt_result.final_output,
                duration_seconds=attempt_result.duration_seconds,
                outcome=outcome,
                assert_passed=judge_result.assert_passed,
                assert_evidence=judge_result.assert_evidence,
                autorater_passed=judge_result.autorater_passed,
                autorater_reasoning=judge_result.autorater_reasoning,
            ),
            task,
            on_attempt_done,
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


def _resolve_skill_path(spec: EvalSpec, spec_path: Path) -> str | None:
    if not spec.skill.path:
        return None
    path = Path(spec.skill.path).expanduser()
    if not path.is_absolute():
        path = spec_path.parent / path
    return str(path.resolve())


# Directories never staged into a run: results (cheat surface), VCS, caches.
_STAGE_EXCLUDE_DIRS = {".caliper", ".git", "__pycache__", "node_modules", ".venv"}
# Per-file cap so a stray large fixture or binary can't bloat every attempt.
_STAGE_MAX_FILE_BYTES = 5 * 1024 * 1024


def _stage_skill_directory(
    skill_path: str, isolated_home: str, forbidden_files: list[str]
) -> None:
    """Stage a directory-based skill's files into the run's working dir.

    Modern skills lean on progressive disclosure: a short ``SKILL.md`` that
    points at ``REFERENCE.md``, ``references/`` and helper scripts the agent
    reads on demand. If we hand the agent only ``SKILL.md``'s text those
    pointers dangle, so we copy the skill directory into ``isolated_home`` (the
    cwd the agent runs in) and the relative links resolve as they would from a
    real install.

    Only a real skill *directory* is staged, keyed off a file named ``SKILL.md``;
    a lone slash-command ``.md`` has no directory and is left alone. Cheat
    surfaces — the ``.eval.yaml`` spec, ``.caliper/`` results, and anything the
    spec marks ``forbidden_files`` — are never copied, so staging cannot leak the
    answer key.
    """
    src = Path(skill_path)
    if src.name != "SKILL.md" or not src.exists():
        return

    skill_dir = src.parent
    home = Path(isolated_home)
    forbidden = [re.compile(p) for p in forbidden_files]

    for item in sorted(skill_dir.rglob("*")):
        if not item.is_file():
            continue
        rel = item.relative_to(skill_dir)
        if any(part in _STAGE_EXCLUDE_DIRS for part in rel.parts):
            continue
        if item.name.endswith(".eval.yaml"):
            continue
        rel_posix = rel.as_posix()
        if any(r.search(rel_posix) or r.search("./" + rel_posix) for r in forbidden):
            continue
        try:
            if item.stat().st_size > _STAGE_MAX_FILE_BYTES:
                continue
        except OSError:
            continue
        dst = home / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item, dst)


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

    def snapshot(self, skill_path: str | None) -> SkillSnapshot:
        if not skill_path:
            return SkillSnapshot(path="", files={})

        path = Path(skill_path).expanduser().resolve()
        if not path.exists():
            return SkillSnapshot(path=str(path), files={})

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
