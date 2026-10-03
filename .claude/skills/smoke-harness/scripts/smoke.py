"""Run caliper's backend smoke evals and check the saved results.

    python .claude/skills/smoke-harness/scripts/smoke.py              # backends this branch touched
    python .claude/skills/smoke-harness/scripts/smoke.py codex pi     # these backends
    python .claude/skills/smoke-harness/scripts/smoke.py claude-code:claude-haiku-4-5-20251001
    python .claude/skills/smoke-harness/scripts/smoke.py --dry-run   # print the plan, run nothing

Exit 0 when every attempt passed, 1 when any check failed, 2 when nothing could
run (no harness-facing change, or no requested backend CLI installed).
"""

from __future__ import annotations

import argparse
import os
import secrets
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
# Check results with this checkout's schema, not whichever caliper is installed;
# `python -m caliper.main` below runs from ROOT for the same reason.
sys.path.insert(0, str(ROOT))

from caliper.harness import get_harness  # noqa: E402
from caliper.schema.results import Outcome, RunResults  # noqa: E402
from caliper.schema.spec import load_spec, parse_target  # noqa: E402

TESTS = ROOT / "tests"

# Which backends each smoke spec runs under, from the "Run it" comment at the top
# of each spec. pi has no MCP by design (docs/adr/0010).
SPECS = {
    "claude-code-smoke.eval.yaml": {"claude-code"},
    "codex-smoke.eval.yaml": {"codex"},
    "hermes-smoke.eval.yaml": {"hermes"},
    "pi-smoke.eval.yaml": {"pi"},
    "mcp-smoke.eval.yaml": {"claude-code", "codex", "hermes"},
    "mcp-header-smoke.eval.yaml": {"claude-code", "hermes"},
    "mcp-remote-smoke.eval.yaml": {"claude-code"},
}
BACKENDS = ["claude-code", "codex", "hermes", "pi"]
BACKEND_FILES = {
    f"caliper/harness/{name.replace('-', '_')}.py": name for name in BACKENDS
}


def changed_files() -> list[str]:
    base = subprocess.run(
        ["git", "merge-base", "origin/main", "HEAD"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    tracked = subprocess.run(
        ["git", "diff", "--name-only", base],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    # A new harness file or smoke spec counts before it is staged.
    untracked = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    return tracked + untracked


def touched_backends(files: list[str]) -> set[str]:
    backends: set[str] = set()
    for path in files:
        if path in BACKEND_FILES:
            backends.add(BACKEND_FILES[path])
        elif path.startswith("caliper/"):
            # Shared code (runner, attempt, workdir, judge, mcp, base) reaches
            # every backend.
            return set(BACKENDS)
        elif path.startswith("tests/") and Path(path).name in SPECS:
            backends |= SPECS[Path(path).name]
        elif path.startswith("tests/fixtures/mcp/"):
            backends |= SPECS["mcp-smoke.eval.yaml"]
    return backends


def check(results_path: Path, backend: str, spec_path: Path) -> list[str]:
    """Every problem in one saved run; empty when the run is clean."""
    if not results_path.exists():
        return ["no results JSON was saved"]
    results = RunResults.model_validate_json(results_path.read_text())
    run = results.run
    problems = []
    # An empty run checks nothing, so it must not read as a pass: every task in
    # the spec needs its one attempt (--k 1).
    attempts = {t.task_name: len(t.attempts) for t in results.task_results}
    for task in load_spec(spec_path).tasks:
        if attempts.get(task.name, 0) != 1:
            problems.append(
                f"{task.name}: expected 1 attempt, got {attempts.get(task.name, 0)}"
            )
    if run.backend != backend:
        problems.append(f"ran on {run.backend}, expected {backend}")
    if run.user_customizations:
        problems.append("ran with user customizations loaded")
    if run.interrupted:
        problems.append("run was interrupted")
    for hook in run.hook_failures:
        problems.append(
            f"{hook.phase} hook exited {hook.exit_code}: {hook.output[-300:]}"
        )
    for task in results.task_results:
        for attempt in task.attempts:
            where = f"{task.task_name} #{attempt.attempt}"
            if attempt.outcome is not Outcome.PASS:
                reason = (
                    attempt.assert_evidence
                    or attempt.autorater_reasoning
                    or attempt.output[-300:]
                )
                problems.append(f"{where}: {attempt.outcome.value}: {reason}")
            if not attempt.transcript:
                problems.append(f"{where}: no transcript was saved")
            for hook in attempt.hook_failures:
                problems.append(f"{where}: {hook.phase} hook exited {hook.exit_code}")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "targets",
        nargs="*",
        help="backend or backend:model; default: the backends this branch touched",
    )
    parser.add_argument("--dry-run", action="store_true", help="print the plan only")
    args = parser.parse_args()

    if args.targets:
        targets = [(parse_target(t)[0] or t, t) for t in args.targets]
    else:
        targets = [(b, b) for b in sorted(touched_backends(changed_files()))]
        if not targets:
            print("No harness-facing change on this branch; name a backend to run.")
            return 2

    plan = []
    for backend, target in targets:
        # caliper's own lookup: the *_CLI_PATH override, install locations, PATH.
        if get_harness(backend).prompt_cli_missing():
            print(f"skip {backend}: caliper cannot find its CLI")
            continue
        for spec, runs_on in SPECS.items():
            if backend in runs_on:
                plan.append((spec, backend, target))
    if not plan:
        print("Nothing to run: no requested backend CLI is installed.")
        return 2

    for spec, _, target in plan:
        print(f"plan: caliper run tests/{spec} --k 1 --model {target}")
    if args.dry_run:
        return 0

    # The header smoke's local echo server checks this token; any value works.
    env = {**os.environ, "MCP_ECHO_TOKEN": secrets.token_hex(8)}
    out_dir = Path(tempfile.mkdtemp(prefix="caliper-smoke-"))
    failed = False
    for spec, backend, target in plan:
        results = out_dir / f"{backend}-{spec.removesuffix('.eval.yaml')}.json"
        subprocess.run(
            [
                sys.executable,
                "-m",
                "caliper.main",
                "run",
                str(TESTS / spec),
                "--k",
                "1",
                "--model",
                target,
                "--output",
                str(results),
            ],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
        )
        problems = check(results, backend, TESTS / spec)
        failed |= bool(problems)
        print(f"{'FAIL' if problems else 'ok  '} {backend:12} {spec}")
        for problem in problems:
            print(f"     - {problem}")
    print(f"results: {out_dir}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
