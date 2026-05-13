from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from verdict.harness.base import AttemptResult, ConversationTurn, HarnessBackend
from verdict.judge.base import Judge, JudgeResult
from verdict.schema.results import (
    AggregateScore,
    AttemptRecord,
    DeltaReport,
    FileSnapshot,
    RunMeta,
    RunResults,
    SkillSnapshot,
    TaskResult,
)
from verdict.schema.spec import EvalSpec, TaskSpec, spec_name
from verdict.scoring import aggregate_scores, compute_delta, pass_at_k


class CheatDetector:
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


class SkillSnapshotter:
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


class TaskRunner:
    def __init__(
        self,
        harness: HarnessBackend,
        judge: Judge,
        spec: EvalSpec,
        spec_path: Path,
        k: int = 3,
        workers: int = 4,
        timeout: int = 120,
        baseline: bool = False,
        on_attempt_done: Callable[[str, int, bool, bool], None] | None = None,
    ) -> None:
        self._harness = harness
        self._judge = judge
        self._spec = spec
        self._spec_path = spec_path
        self._k = k
        self._workers = workers
        self._timeout = timeout
        self._baseline = baseline
        self._on_attempt_done = on_attempt_done

        auto_forbidden = [
            re.escape(str(spec_path.resolve())),
            re.escape(str((spec_path.parent / ".verdict").resolve())),
        ]
        all_patterns = list(spec.sandbox.forbidden_files) + auto_forbidden
        self._cheat = CheatDetector(all_patterns)
        self._snapshotter = SkillSnapshotter()

    def run(self) -> RunResults:
        spec = self._spec
        skill_snapshot = self._snapshotter.snapshot(spec.skill.path)

        task_results_with: list[TaskResult] = []
        task_results_without: list[TaskResult] = []

        with ThreadPoolExecutor(max_workers=self._workers) as pool:
            futures_with = {
                pool.submit(self._run_task, task, with_skill=True): task
                for task in spec.tasks
            }
            futures_without = {}
            if self._baseline:
                futures_without = {
                    pool.submit(self._run_task, task, with_skill=False): task
                    for task in spec.tasks
                }

            for fut in as_completed(list(futures_with) + list(futures_without)):
                result = fut.result()
                if fut in futures_with:
                    task_results_with.append(result)
                else:
                    task_results_without.append(result)

        task_results_with.sort(key=lambda r: r.task_id)
        task_results_without.sort(key=lambda r: r.task_id)

        pass_counts_with = {
            r.task_id: (r.task_name, r.successes, self._k) for r in task_results_with
        }
        agg_with = aggregate_scores(pass_counts_with)

        agg_without: AggregateScore | None = None
        delta: DeltaReport | None = None
        if self._baseline and task_results_without:
            pass_counts_without = {
                r.task_id: (r.task_name, r.successes, self._k) for r in task_results_without
            }
            agg_without = aggregate_scores(pass_counts_without)
            delta = compute_delta(agg_with, agg_without)

        return RunResults(
            run=RunMeta(
                spec=spec_name(self._spec_path),
                timestamp=datetime.now(tz=timezone.utc),
                k=self._k,
                judge_strategy="autorater",
                backend=spec.skill.backend,
                model=spec.skill.model,
            ),
            skill_snapshot=skill_snapshot,
            task_results=task_results_with,
            aggregate=agg_with,
            baseline=agg_without,
            delta=delta,
        )

    def _run_task(self, task: TaskSpec, *, with_skill: bool) -> TaskResult:
        attempts: list[AttemptRecord] = []
        for attempt_num in range(1, self._k + 1):
            record = self._run_attempt(task, attempt_num, with_skill=with_skill)
            attempts.append(record)

        successes = sum(1 for a in attempts if a.passed)
        return TaskResult(
            task_id=task.id,
            task_name=task.name,
            attempts=attempts,
            successes=successes,
            pass_at_k=pass_at_k(successes, self._k),
        )

    def _run_attempt(self, task: TaskSpec, attempt: int, *, with_skill: bool) -> AttemptRecord:
        tmp_dir = tempfile.mkdtemp(prefix="verdict-")
        try:
            self._run_shell(task.setup)
            attempt_result = self._harness.run(
                task_id=task.id,
                attempt=attempt,
                prompt=task.prompt,
                skill_path=self._spec.skill.path if with_skill else None,
                model=self._spec.skill.model,
                timeout=self._timeout,
                isolated_home=tmp_dir,
            )

            cheat_violations = self._cheat.check(attempt_result.transcript)
            if cheat_violations:
                if self._on_attempt_done:
                    self._on_attempt_done(task.id, attempt, False, True)
                return AttemptRecord(
                    attempt=attempt,
                    output=attempt_result.final_output,
                    duration_seconds=attempt_result.duration_seconds,
                    passed=False,
                    cheated=True,
                    cheat_evidence=cheat_violations,
                )

            judge_result = self._judge.evaluate(
                task=task,
                transcript=attempt_result.transcript,
                final_output=attempt_result.final_output,
                spec_dir=str(self._spec_path.parent),
            )

            passed = judge_result.passed
            if self._on_attempt_done:
                self._on_attempt_done(task.id, attempt, passed, False)

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
            self._run_shell(task.cleanup)
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def _run_shell(self, cmd: str | None) -> None:
        if cmd:
            subprocess.run(cmd, shell=True, check=False)
