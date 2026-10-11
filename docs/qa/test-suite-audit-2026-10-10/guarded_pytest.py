from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
AGENTS = {"codex", "claude", "hermes", "pi", "npm", "npx"}


def check_command(command):
    first = command[0] if isinstance(command, (list, tuple)) else command.split()[0]
    name = Path(str(first)).name.lower().removesuffix(".cmd").removesuffix(".exe")
    if name in AGENTS:
        raise AssertionError(f"audit guard blocked real external CLI: {name}")


original_run = subprocess.run
original_popen = subprocess.Popen


def guarded_run(command, *args, **kwargs):
    check_command(command)
    return original_run(command, *args, **kwargs)


def guarded_popen(command, *args, **kwargs):
    check_command(command)
    return original_popen(command, *args, **kwargs)


subprocess.run = guarded_run
subprocess.Popen = guarded_popen
for key in ("CODEX_CLI_PATH", "CLAUDE_CLI_PATH", "HERMES_CLI_PATH", "PI_CLI_PATH"):
    os.environ.pop(key, None)

sys.path.insert(0, str(ROOT))
if "--audit-tracked-specs" in sys.argv:
    sys.argv.remove("--audit-tracked-specs")
    tracked = subprocess.check_output(
        ["git", "ls-files", "*.eval.yaml"], cwd=ROOT, text=True
    ).splitlines()
    original_rglob = Path.rglob

    def tracked_rglob(self, pattern, *args, **kwargs):
        if self.resolve() == ROOT and pattern == "*.eval.yaml":
            return iter(ROOT / name for name in tracked)
        return original_rglob(self, pattern, *args, **kwargs)

    Path.rglob = tracked_rglob
raise SystemExit(pytest.main(sys.argv[1:]))
