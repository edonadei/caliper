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
from caliper.schema.results import (
    AggregateScore,
    AttemptRecord,
    DeltaReport,
    FileSnapshot,
    RunMeta,
    RunResults,
    SkillSnapshot,
    TaskResult,
)
from caliper.schema.spec import EvalSpec, TaskSpec, spec_name
from caliper.scoring import aggregate_scores, compute_delta, pass_at_k


@dataclass
class AttemptEvent:
    task_id: str
    attempt: int
    passed: bool
    cheated: bool


def run(
    spec: EvalSpec,
    spec_path: Path,
    harness: HarnessBackend,
    judge: Judge,
    k: int = 3,
    workers: int = 4,
    timeout: int = 120,
    baseline: bool = False,
    on_attempt_done: Callable[[AttemptEvent], None] | None = None,
) -> RunResults:
    skill_snapshot = _SkillSnapshotter().snapshot(_resolve_skill_path(spec, spec_path))

    auto_forbidden = [
        re.escape(str(spec_path.resolve())),
        re.escape(str((spec_path.parent / ".caliper").resolve())),
    ]
    cheat = _CheatDetector(list(spec.sandbox.forbidden_files) + auto_forbidden)

    task_results_with: list[TaskResult] = []
    task_results_without: list[TaskResult] = []

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures_with = {
            pool.submit(
                _run_task, task, harness, judge, cheat, spec, spec_path,
                k, timeout, True, on_attempt_done,
            ): task
            for task in spec.tasks
        }
        futures_without = (
            {
                pool.submit(
                    _run_task, task, harness, judge, cheat, spec, spec_path,
                    k, timeout, False, on_attempt_done,
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
        r.task_id: (r.task_name, r.successes, k) for r in task_results_with
    }
    agg_with = aggregate_scores(pass_counts_with)

    agg_without: AggregateScore | None = None
    delta: DeltaReport | None = None
    if baseline and task_results_without:
        pass_counts_without = {
            r.task_id: (r.task_name, r.successes, k) for r in task_results_without
        }
        agg_without = aggregate_scores(pass_counts_without)
        delta = compute_delta(agg_with, agg_without)

    return RunResults(
        run=RunMeta(
            spec=spec_name(spec_path),
            timestamp=datetime.now(tz=timezone.utc),
            k=k,
            backend=spec.skill.backend,
            model=spec.skill.model,
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
) -> TaskResult:
    attempts: list[AttemptRecord] = []
    for attempt_num in range(1, k + 1):
        record = _run_attempt(
            task, attempt_num, harness, judge, cheat,
            spec, spec_path, timeout, with_skill, on_attempt_done,
        )
        attempts.append(record)

    successes = sum(1 for a in attempts if a.passed)
    return TaskResult(
        task_id=task.id,
        task_name=task.name,
        attempts=attempts,
        successes=successes,
        pass_at_k=pass_at_k(successes, k),
    )


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
) -> AttemptRecord:
    tmp_dir = tempfile.mkdtemp(prefix="caliper-")
    try:
        _run_shell(task.setup)
        resolved_extra_path = [
            str((spec_path.parent / p).resolve())
            for p in spec.sandbox.extra_path
        ]
        skill_path = _resolve_skill_path(spec, spec_path) if with_skill else None
        attempt_result = harness.run(
            task_id=task.id,
            attempt=attempt,
            prompt=task.prompt,
            skill_path=skill_path,
            model=spec.skill.model,
            timeout=timeout,
            isolated_home=tmp_dir,
            extra_path=resolved_extra_path,
        )

        if attempt_result.exit_code != 0:
            error = attempt_result.error or f"harness exited {attempt_result.exit_code}"
            if on_attempt_done:
                on_attempt_done(AttemptEvent(task_id=task.id, attempt=attempt, passed=False, cheated=False))
            return AttemptRecord(
                attempt=attempt,
                output=attempt_result.final_output,
                duration_seconds=attempt_result.duration_seconds,
                passed=False,
                cheated=False,
                assert_passed=False,
                assert_evidence=error,
            )

        cheat_violations = cheat.check(attempt_result.transcript)
        if cheat_violations:
            if on_attempt_done:
                on_attempt_done(AttemptEvent(task_id=task.id, attempt=attempt, passed=False, cheated=True))
            return AttemptRecord(
                attempt=attempt,
                output=attempt_result.final_output,
                duration_seconds=attempt_result.duration_seconds,
                passed=False,
                cheated=True,
                cheat_evidence=cheat_violations,
            )

        judge_result = judge.evaluate(
            task=task,
            transcript=attempt_result.transcript,
            final_output=attempt_result.final_output,
            spec_dir=str(spec_path.parent),
        )

        passed = judge_result.passed
        if on_attempt_done:
            on_attempt_done(AttemptEvent(task_id=task.id, attempt=attempt, passed=passed, cheated=False))

        return AttemptRecord(
            attempt=attempt,
            output=attempt_result.final_output,
            duration_seconds=attempt_result.duration_seconds,
            passed=passed,
            cheated=False,
            assert_passed=judge_result.assert_passed,
            assert_evidence=judge_result.assert_evidence,
            autorater_passed=judge_result.autorater_passed,
            autorater_reasoning=judge_result.autorater_reasoning,
        )
    finally:
        _run_shell(task.cleanup)
        shutil.rmtree(tmp_dir, ignore_errors=True)


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
